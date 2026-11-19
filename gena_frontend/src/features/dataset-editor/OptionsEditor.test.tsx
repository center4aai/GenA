import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import i18n from '@/shared/i18n';
import { draftFromQuestion } from '@/shared/lib/questionDraft';
import { OptionsEditor } from './OptionsEditor';

const t = (key: string, opts?: Record<string, unknown>) => String(i18n.t(key, opts as never));

function draft(n: number, correct: number[], type: 'one' | 'multi' = 'one') {
  const options = Object.fromEntries(Array.from({ length: n }, (_, i) => [`option_${i + 1}`, `opt ${i + 1}`]));
  return draftFromQuestion({ question_type: type, options, correct_answer: correct.join(',') });
}

describe('OptionsEditor', () => {
  it('offers removal only above four options and adding only below nine', () => {
    const { rerender } = render(<OptionsEditor draft={draft(4, [1])} readOnly={false} onChange={() => {}} />);
    expect(screen.queryByLabelText(t('results:removeOption', { n: 1 }))).toBeNull();
    expect(screen.getByText(new RegExp(t('results:addOption')))).toBeTruthy();

    rerender(<OptionsEditor draft={draft(5, [1])} readOnly={false} onChange={() => {}} />);
    expect(screen.getByLabelText(t('results:removeOption', { n: 5 }))).toBeTruthy();

    rerender(<OptionsEditor draft={draft(9, [1])} readOnly={false} onChange={() => {}} />);
    expect(screen.queryByText(new RegExp(t('results:addOption')))).toBeNull();
  });

  it('keeps single-choice marks exclusive', () => {
    const onChange = vi.fn();
    render(<OptionsEditor draft={draft(4, [4])} readOnly={false} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText(t('results:correctOptionN', { n: 2 })));
    expect(onChange).toHaveBeenCalledWith({ correct: [2], touchedCorrect: true });
  });

  it('caps multi marks at count minus one and asks for at least two', () => {
    const { rerender } = render(
      <OptionsEditor draft={draft(4, [1, 2, 3], 'multi')} readOnly={false} onChange={() => {}} />,
    );
    const fourth = screen.getByLabelText(t('results:correctOptionN', { n: 4 })) as HTMLInputElement;
    expect(fourth.disabled).toBe(true);

    rerender(<OptionsEditor draft={draft(4, [1], 'multi')} readOnly={false} onChange={() => {}} />);
    expect(screen.getByText(t('results:minTwoCorrect'))).toBeTruthy();
  });

  it('renumbers and remaps marks when an option is removed', () => {
    const onChange = vi.fn();
    render(<OptionsEditor draft={draft(5, [5])} readOnly={false} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText(t('results:removeOption', { n: 2 })));
    expect(onChange).toHaveBeenCalledWith({
      optionTexts: ['opt 1', 'opt 3', 'opt 4', 'opt 5'],
      correct: [4],
      touchedCorrect: true,
    });
  });

  it('asks for four options first when an open question was switched to a choice kind', () => {
    const onChange = vi.fn();
    const switched = { ...draftFromQuestion({ question_type: 'open', options: '', correct_answer: 'x' }), question_type: 'one' as const };
    render(<OptionsEditor draft={switched} readOnly={false} onChange={onChange} />);
    expect(screen.getByText(t('results:minOptionsHint'))).toBeTruthy();
    expect(screen.queryByText(t('results:selectCorrectOption'))).toBeNull();
    fireEvent.click(screen.getByText(new RegExp(t('results:addOption'))));
    expect(onChange).toHaveBeenCalledWith({ optionTexts: [''] });
  });

  it('asks to keep one mark when a multi was switched to single, and a click does exactly that', () => {
    const onChange = vi.fn();
    const switched = { ...draft(4, [1, 3], 'multi'), question_type: 'one' as const };
    render(<OptionsEditor draft={switched} readOnly={false} onChange={onChange} />);
    expect(screen.getByText(t('results:singleCorrectHint'))).toBeTruthy();
    fireEvent.click(screen.getByLabelText(t('results:correctOptionN', { n: 1 })));
    expect(onChange).toHaveBeenCalledWith({ correct: [1], touchedCorrect: true });
  });

  it('renders read-only without any editing controls', () => {
    render(<OptionsEditor draft={draft(6, [1, 2], 'multi')} readOnly onChange={() => {}} />);
    expect(screen.queryByLabelText(t('results:removeOption', { n: 1 }))).toBeNull();
    expect(screen.queryByText(new RegExp(t('results:addOption')))).toBeNull();
    const first = screen.getByLabelText(t('results:optionN', { n: 1 })) as HTMLTextAreaElement;
    expect(first.disabled).toBe(true);
  });
});
