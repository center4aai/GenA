import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import i18n from '@/shared/i18n';
import { emptyFilters, withFilterValues } from '@/shared/lib/questionFilters';
import { QuestionFilters } from './QuestionFilters';

const t = (key: string) => String(i18n.t(key));

describe('QuestionFilters', () => {
  it('enables Apply only when the pending filters differ, and Reset once something is applied', () => {
    const noop = vi.fn();
    const empty = emptyFilters();
    const { rerender } = render(
      <QuestionFilters pending={empty} applied={empty} chunkOptions={['1', '2']} onPendingChange={noop} onApply={noop} onReset={noop} />,
    );
    const apply = () => screen.getByText(t('results:applyFilters')) as HTMLButtonElement;
    const reset = () => screen.getByText(t('results:resetFilters')) as HTMLButtonElement;
    expect(apply().disabled).toBe(true);
    // Always in the DOM so the button column keeps its width; just inert.
    expect(reset().disabled).toBe(true);

    const pending = withFilterValues(emptyFilters(), 'types', ['one']);
    rerender(
      <QuestionFilters pending={pending} applied={empty} chunkOptions={['1', '2']} onPendingChange={noop} onApply={noop} onReset={noop} />,
    );
    expect(apply().disabled).toBe(false);

    rerender(
      <QuestionFilters pending={pending} applied={pending} chunkOptions={['1', '2']} onPendingChange={noop} onApply={noop} onReset={noop} />,
    );
    expect(apply().disabled).toBe(true);
    expect(reset().disabled).toBe(false);
  });

  it('lists chunks by bare number and offers the export-exclusion bucket', () => {
    const noop = vi.fn();
    const empty = emptyFilters();
    render(
      <QuestionFilters pending={empty} applied={empty} chunkOptions={['3', '12']} onPendingChange={noop} onApply={noop} onReset={noop} />,
    );
    expect(screen.getByText('12')).toBeTruthy();
    expect(screen.queryByText(/12/)?.textContent).toBe('12');
    expect(screen.getByText(t('results:filterExcluded'))).toBeTruthy();
  });
});
