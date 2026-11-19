import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { formatTimestamp } from '@/shared/lib/dateTime';
import { revalidationHeaderStatus, revalidationTone, REVALIDATION_SCOPES } from '@/shared/lib/revalidationState';
import type { LlmModel, RevalidationScope, RevalidationState } from '@/shared/types/documents';
import { CollapsibleSection } from '@/shared/ui/CollapsibleSection';
import { ModelSelect, modelLabel } from '@/shared/ui/ModelSelect';
import { pickModelId } from '@/shared/lib/modelAvailability';
import { StatusBanner } from '@/shared/ui/StatusBanner';

const SCOPE_KEYS: Record<RevalidationScope, string> = {
  all: 'results:revalScopeAll',
  required: 'results:revalScopeRequired',
  user_edited: 'results:revalScopeUserEdited',
  paraphrased: 'results:revalScopeParaphrased',
};

const RUN_STATUS_KEYS = {
  completed: 'results:revalRunCompleted',
  completed_with_errors: 'results:revalRunCompletedWithErrors',
  failed: 'results:revalRunFailed',
  processing: 'results:revalRunProcessing',
} as const;

/**
 * «Повторная проверка качества» in the question-set card: status of the
 * selected version, last run, scope choice, start and technical retry.
 * Shown only for sets that are ready (the caller decides).
 */
export function RevalidationAccordion({
  state,
  versionLabel,
  unsavedCount,
  canStart,
  starting,
  error,
  models = [],
  labelModels = models,
  defaultModelId,
  onStart,
  onRetry,
  onDownloadRun,
  downloading = false,
}: {
  state: RevalidationState;
  /** «Версия N от …» — the version the run applies to (ТЗ 3). */
  versionLabel: string;
  unsavedCount: number;
  /** Expert role: plain users see the status only. */
  canStart: boolean;
  starting: boolean;
  error: string | null;
  /** Validators to choose from: the available ones (same list as for a new generation). */
  models?: LlmModel[];
  /** Every known model, to name the validator of a run even when it is down now. */
  labelModels?: LlmModel[];
  /** Preselected validator: the set's own, when it is still in the list. */
  defaultModelId?: string | null;
  onStart: (scope: RevalidationScope, validationModelId?: string) => void;
  onRetry: () => void;
  /** Download the finished run's before/after verdicts (CSV). */
  onDownloadRun?: (runId: string) => void;
  downloading?: boolean;
}) {
  const { t } = useTranslation(['results', 'common']);
  const firstAvailable = REVALIDATION_SCOPES.find((s) => state.scope_counts[s] > 0) ?? 'all';
  const [scope, setScope] = useState<RevalidationScope>(
    state.scope_counts.required > 0 ? 'required' : firstAvailable,
  );
  const [unsavedWarning, setUnsavedWarning] = useState(false);
  const [modelId, setModelId] = useState('');
  const chosenModelId = pickModelId(models, modelId, defaultModelId);
  const active = state.active_run;
  const last = state.last_run;
  const chosen = state.scope_counts[scope] > 0 ? scope : firstAvailable;

  const header = (
    <span>
      {t('results:revalTitle')} | {revalidationHeaderStatus(state, t)}
    </span>
  );

  const start = () => {
    // Unsaved edits anywhere in the version block the run, whatever the scope (ТЗ 5).
    if (unsavedCount > 0) {
      setUnsavedWarning(true);
      return;
    }
    setUnsavedWarning(false);
    onStart(chosen, chosenModelId || undefined);
  };

  return (
    <CollapsibleSection title={header} tone={revalidationTone(state.primary_status)}>
      <div className="space-y-3 text-sm">
        <p className="text-xs text-slate-500">{versionLabel}</p>

        {active ? (
          <p className="font-medium text-sky-900">
            {active.validation_model_id
              ? t('results:revalInProgressWithModel', {
                  x: active.succeeded,
                  y: active.total,
                  model: modelLabel(labelModels, active.validation_model_id),
                })
              : t('results:revalInProgress', { x: active.succeeded, y: active.total })}
          </p>
        ) : last ? (
          <div className="space-y-0.5 text-slate-700">
            <p>
              {last.validation_model_id
                ? t('results:revalLastRunWithModel', {
                    status: t(RUN_STATUS_KEYS[last.status]),
                    at: formatTimestamp(last.finished_at ?? last.created_at),
                    model: modelLabel(labelModels, last.validation_model_id),
                  })
                : t('results:revalLastRun', {
                    status: t(RUN_STATUS_KEYS[last.status]),
                    at: formatTimestamp(last.finished_at ?? last.created_at),
                  })}
            </p>
            {last.status !== 'failed' && (
              <p>{t('results:revalRevalidated', { x: last.succeeded, y: last.total })}</p>
            )}
            {last.errors > 0 && last.status !== 'failed' && (
              <p>{t('results:revalErrors', { n: last.errors })}</p>
            )}
            {onDownloadRun && (
              <button
                type="button"
                className="btn-secondary mt-1"
                disabled={downloading}
                onClick={() => onDownloadRun(last.run_id)}
              >
                {t('results:revalDownloadRun')}
              </button>
            )}
          </div>
        ) : (
          <p className="text-slate-600">{t('results:revalNotRunYet')}</p>
        )}

        {!active && canStart && (
          <>
            {state.retryable_count > 0 && (
              <button type="button" className="btn-secondary" disabled={starting} onClick={onRetry}>
                {last?.status === 'failed'
                  ? t('results:revalRetry')
                  : t('results:revalRetryN', { count: state.retryable_count })}
              </button>
            )}

            <fieldset className="space-y-1">
              <legend className="mb-1 font-medium text-slate-800">{t('results:revalSelectScope')}</legend>
              {REVALIDATION_SCOPES.map((s) => {
                const n = state.scope_counts[s];
                return (
                  <label key={s} className={`flex items-center gap-2 ${n ? '' : 'text-slate-400'}`}>
                    <input
                      type="radio"
                      name="revalidation-scope"
                      value={s}
                      checked={chosen === s}
                      disabled={n === 0}
                      onChange={() => setScope(s)}
                    />
                    {t(SCOPE_KEYS[s], { n })}
                  </label>
                );
              })}
            </fieldset>

            {models.length > 0 && (
              <div>
                <label htmlFor="revalidation-model" className="mb-1 block font-medium text-slate-800">
                  {t('results:revalModel')}
                </label>
                <ModelSelect
                  id="revalidation-model"
                  className="input-field max-w-md"
                  models={models}
                  value={chosenModelId}
                  onChange={setModelId}
                />
              </div>
            )}

            <button
              type="button"
              className="btn-primary"
              disabled={starting || state.scope_counts[chosen] === 0}
              onClick={start}
            >
              {t('results:revalStart')}
            </button>
          </>
        )}

        {unsavedWarning && unsavedCount > 0 && (
          <StatusBanner tone="warning">{t('results:revalUnsavedChanges')}</StatusBanner>
        )}
        {error && <StatusBanner tone="error">{error}</StatusBanner>}
      </div>
    </CollapsibleSection>
  );
}
