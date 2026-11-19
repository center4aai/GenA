import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import i18n from '@/shared/i18n';
import { draftFromQuestion } from '@/shared/lib/questionDraft';
import type { Question } from '@/shared/types/documents';
import { QuestionCard, type QuestionCardProps } from './QuestionCard';

const t = (key: string, opts?: Record<string, unknown>) => String(i18n.t(key, opts as never));

const Q: Question = {
  question_id: '3f2a9c1e-5d3b-4c2a-9b1e-2d4f6a8c0e12',
  question_type: 'multi',
  chunk_id: 2,
  task: 'Which two?',
  options: { option_1: 'a', option_2: 'b', option_3: 'c', option_4: 'd' },
  correct_answer: '1,3',
  provocativeness: '1',
  difficulty: '2',
  validation_passed: 'True',
};

function renderCard(over: Partial<QuestionCardProps> = {}) {
  const props: QuestionCardProps = {
    question: Q,
    index: 0,
    readOnly: false,
    saving: false,
    saved: false,
    saveError: null,
    exportPending: false,
    onDraftChange: vi.fn(),
    onSave: vi.fn(),
    onCancel: vi.fn(),
    onToggleExport: vi.fn(),
    ...over,
  };
  render(<QuestionCard {...props} />);
  return props;
}

describe('QuestionCard', () => {
  it('shows Save and Cancel only once the card has a draft', () => {
    renderCard();
    expect(screen.queryByText(t('results:saveChanges'))).toBeNull();
    expect(screen.queryByText(t('common:cancel'))).toBeNull();
  });

  it('offers Save and Cancel for a dirty draft and wires them through', () => {
    const props = renderCard({ draft: { ...draftFromQuestion(Q), task: 'Which two exactly?' } });
    fireEvent.click(screen.getByText(t('results:saveChanges')));
    fireEvent.click(screen.getByText(t('common:cancel')));
    expect(props.onSave).toHaveBeenCalledTimes(1);
    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  it('saves the export switch immediately without opening Save', () => {
    const props = renderCard();
    fireEvent.click(screen.getByRole('switch'));
    expect(props.onToggleExport).toHaveBeenCalledWith(false);
    expect(screen.queryByText(t('results:saveChanges'))).toBeNull();
  });

  it('disables Save with a hint when a multi has fewer than two correct marks', () => {
    renderCard({ draft: { ...draftFromQuestion(Q), correct: [1], touchedCorrect: true } });
    const save = screen.getByText(t('results:saveChanges')) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.getByText(t('results:minTwoCorrect'))).toBeTruthy();
  });

  it('blocks Save after multi → single until the extra marks are removed', () => {
    renderCard({ draft: { ...draftFromQuestion(Q), question_type: 'one' } });
    const save = screen.getByText(t('results:saveChanges')) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.getByText(t('results:singleCorrectHint'))).toBeTruthy();
  });

  it('carries the marked options over as the answer text when switching to open', () => {
    const props = renderCard();
    const typeSelect = screen.getByDisplayValue(t('create:typeMulti')) as HTMLSelectElement;
    fireEvent.change(typeSelect, { target: { value: 'open' } });
    expect(props.onDraftChange).toHaveBeenCalledWith({ question_type: 'open', correct_answer: 'a, c' });
  });

  it('blocks Save after open → single until four options exist', () => {
    const open: Question = { ...Q, question_type: 'open', options: '', correct_answer: 'Because.' };
    renderCard({ question: open, draft: { ...draftFromQuestion(open), question_type: 'one' } });
    const save = screen.getByText(t('results:saveChanges')) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.getByText(t('results:minOptionsHint'))).toBeTruthy();
  });

  it('renders read-only for non-experts: no switch, disabled fields', () => {
    renderCard({ readOnly: true, draft: undefined });
    expect(screen.queryByRole('switch')).toBeNull();
    const task = screen.getByLabelText(t('results:task')) as HTMLTextAreaElement;
    expect(task.disabled).toBe(true);
  });

  it('shows the quality verdict and the edit status as pills', () => {
    renderCard({ question: { ...Q, edit_status: 'paraphrased' } });
    expect(screen.getByText(t('results:passed'))).toBeTruthy();
    expect(screen.getByText(t('results:editStatusParaphrased'))).toBeTruthy();
    expect(screen.getByText(t('results:fromChunk', { n: 2 }))).toBeTruthy();
  });

  it('keeps questions without a server id read-only', () => {
    renderCard({ question: { ...Q, question_id: 'ds:00001' } });
    expect(screen.getByText(t('results:noQuestionId'))).toBeTruthy();
    const task = screen.getByLabelText(t('results:task')) as HTMLTextAreaElement;
    expect(task.disabled).toBe(true);
  });
});
