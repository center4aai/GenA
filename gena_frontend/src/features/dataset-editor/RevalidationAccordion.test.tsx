import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import i18n from '@/shared/i18n';
import type { RevalidationState } from '@/shared/types/documents';
import { RevalidationAccordion } from './RevalidationAccordion';

const t = (key: string, opts?: Record<string, unknown>) => String(i18n.t(key, opts as never));

function state(partial: Partial<RevalidationState> = {}): RevalidationState {
  return {
    dataset_id: 'd',
    version: 2,
    available: true,
    primary_status: 'required',
    required_count: 3,
    total_count: 10,
    scope_counts: { all: 10, required: 3, user_edited: 3, paraphrased: 0 },
    active_run: null,
    last_run: null,
    retryable_count: 0,
    ...partial,
  };
}

function renderAccordion(s: RevalidationState, over: Record<string, unknown> = {}) {
  const onStart = vi.fn();
  const onRetry = vi.fn();
  render(
    <RevalidationAccordion
      state={s}
      versionLabel="v2"
      unsavedCount={0}
      canStart
      starting={false}
      error={null}
      onStart={onStart}
      onRetry={onRetry}
      {...over}
    />,
  );
  return { onStart, onRetry };
}

describe('RevalidationAccordion', () => {
  it('shows the status in the header and starts the "required" scope by default', () => {
    const { onStart } = renderAccordion(state());
    expect(screen.getByText(`${t('results:revalTitle')} | ${t('results:revalStatusRequired', { n: 3, m: 10 })}`)).toBeTruthy();
    expect((screen.getByLabelText(t('results:revalScopeParaphrased', { n: 0 })) as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByText(t('results:revalStart')));
    expect(onStart).toHaveBeenCalledWith('required', undefined); // no model list → the set's validator
  });

  it('refuses to start while the version has unsaved edits', () => {
    const { onStart } = renderAccordion(state(), { unsavedCount: 2 });
    fireEvent.click(screen.getByText(t('results:revalStart')));
    expect(onStart).not.toHaveBeenCalled();
    expect(screen.getByText(t('results:revalUnsavedChanges'))).toBeTruthy();
  });

  it('hides scope, start and retry while a run is in progress', () => {
    const run = { run_id: 'r', status: 'processing' as const, scope: 'all' as const, total: 20, succeeded: 17, errors: 0 };
    renderAccordion(state({ primary_status: 'processing', active_run: run, last_run: run }));
    expect(screen.getByText(t('results:revalInProgress', { x: 17, y: 20 }))).toBeTruthy();
    expect(screen.queryByText(t('results:revalStart'))).toBeNull();
    expect(screen.queryByText(t('results:revalSelectScope'))).toBeNull();
  });

  it('offers "Retry N" after a run with technical errors, alongside a new start', () => {
    const last = { run_id: 'r', status: 'completed_with_errors' as const, scope: 'all' as const, total: 20, succeeded: 17, errors: 3 };
    const { onRetry } = renderAccordion(state({ last_run: last, retryable_count: 3 }));
    fireEvent.click(screen.getByText(t('results:revalRetryN', { count: 3 })));
    expect(onRetry).toHaveBeenCalled();
    expect(screen.getByText(t('results:revalStart'))).toBeTruthy();
    expect(screen.getByText(t('results:revalErrors', { n: 3 }))).toBeTruthy();
  });

  const models = [
    { id: 'qwen3-6-27b-ws5', name: 'Qwen 3.6 27B' },
    { id: 'gemma4-26b-litellm', name: 'Gemma 4 26B-A4B' },
  ];

  it("preselects the set's validator and starts with the chosen model", () => {
    const { onStart } = renderAccordion(state(), {
      models,
      defaultModelId: 'qwen3-6-27b-ws5',
    });
    const select = screen.getByLabelText(t('results:revalModel')) as HTMLSelectElement;
    expect(select.value).toBe('qwen3-6-27b-ws5');
    fireEvent.change(select, { target: { value: 'gemma4-26b-litellm' } });
    fireEvent.click(screen.getByText(t('results:revalStart')));
    expect(onStart).toHaveBeenCalledWith('required', 'gemma4-26b-litellm');
  });

  it('falls back to the first model when the set validator is no longer listed', () => {
    renderAccordion(state(), { models, defaultModelId: 'llamacpp-qwen3-6-27b-ws5' });
    expect((screen.getByLabelText(t('results:revalModel')) as HTMLSelectElement).value).toBe('qwen3-6-27b-ws5');
  });

  it("does not preselect the set's validator once it is down and offers only available models", () => {
    renderAccordion(state(), { models: [models[1]], labelModels: models, defaultModelId: 'qwen3-6-27b-ws5' });
    const select = screen.getByLabelText(t('results:revalModel')) as HTMLSelectElement;
    expect(select.value).toBe('gemma4-26b-litellm');
    expect([...select.options].map((o) => o.value)).toEqual(['gemma4-26b-litellm']);
  });

  it('switches to the first available model when the chosen one goes down', () => {
    const props = {
      state: state(), versionLabel: 'v2', unsavedCount: 0, canStart: true, starting: false, error: null,
      onStart: vi.fn(), onRetry: vi.fn(),
    };
    const { rerender } = render(<RevalidationAccordion {...props} models={models} />);
    const select = () => screen.getByLabelText(t('results:revalModel')) as HTMLSelectElement;
    fireEvent.change(select(), { target: { value: 'gemma4-26b-litellm' } });
    rerender(<RevalidationAccordion {...props} models={[models[0]]} labelModels={models} />);
    expect(select().value).toBe('qwen3-6-27b-ws5');
    fireEvent.click(screen.getByText(t('results:revalStart')));
    expect(props.onStart).toHaveBeenCalledWith('required', 'qwen3-6-27b-ws5');
  });

  it('names the validator of a run even when that model is down now', () => {
    const run = {
      run_id: 'r', status: 'processing' as const, scope: 'all' as const, total: 20, succeeded: 5, errors: 0,
      validation_model_id: 'gemma4-26b-litellm',
    };
    renderAccordion(state({ primary_status: 'processing', active_run: run, last_run: run }), {
      models: [models[0]],
      labelModels: models,
    });
    expect(
      screen.getByText(t('results:revalInProgressWithModel', { x: 5, y: 20, model: 'Gemma 4 26B-A4B' })),
    ).toBeTruthy();
  });

  it('hides the model choice during a run and names the model in the progress line', () => {
    const run = {
      run_id: 'r', status: 'processing' as const, scope: 'all' as const, total: 20, succeeded: 5, errors: 0,
      validation_model_id: 'gemma4-26b-litellm',
    };
    renderAccordion(state({ primary_status: 'processing', active_run: run, last_run: run }), { models });
    expect(screen.queryByLabelText(t('results:revalModel'))).toBeNull();
    expect(
      screen.getByText(t('results:revalInProgressWithModel', { x: 5, y: 20, model: 'Gemma 4 26B-A4B' })),
    ).toBeTruthy();
  });

  it('shows the model of the last run (raw id if unknown) and downloads its results', () => {
    const last = {
      run_id: 'run-42', status: 'completed' as const, scope: 'all' as const, total: 4, succeeded: 4, errors: 0,
      finished_at: null, created_at: null, validation_model_id: 'retired-model',
    };
    const onDownloadRun = vi.fn();
    renderAccordion(state({ primary_status: 'completed', last_run: last }), { models, onDownloadRun });
    expect(screen.getByText(/retired-model/)).toBeTruthy();
    fireEvent.click(screen.getByText(t('results:revalDownloadRun')));
    expect(onDownloadRun).toHaveBeenCalledWith('run-42');
  });
});
