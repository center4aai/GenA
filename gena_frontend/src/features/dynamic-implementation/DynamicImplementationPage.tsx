import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listDatasets,
  getDataset,
  listDatasetVersions,
  updateDataset,
  getRevalidationState,
} from '@/shared/api/dataset';
import { rephraseQuestions } from '@/shared/api/agent';
import { useSelectableModels } from '@/shared/api/useSelectableModels';
import { pickModelId } from '@/shared/lib/modelAvailability';
import { HttpError } from '@/shared/api/http';
import type { Question } from '@/shared/types/documents';
import { questionToCreateRequest } from '@/shared/lib/questionPayload';
import { shuffleQuestions } from '@/shared/lib/shuffleOptions';
import { normOptions, optionsEntries, isCorrectOption } from '@/shared/lib/optionsFormat';
import { questionTypeLabel } from '@/shared/lib/labels';
import { useAuth } from '@/features/auth/authStore';
import { useTranslation } from 'react-i18next';
import { useBlocker } from 'react-router-dom';
import { usePageTitle } from '@/shared/i18n/usePageTitle';
import { PageHeader } from '@/shared/ui/PageHeader';
import { StatusBanner, LoadingState, EmptyState } from '@/shared/ui/StatusBanner';
import { markNeedsRevalidation } from '@/shared/lib/revalidation';
import { isRevalidating } from '@/shared/lib/revalidationState';
import { withTransformEditStatus } from '@/shared/lib/questionEdits';

const PAGE_SIZE = 10;

/** Mirrors FINISHED_DATASET_STATUSES in dataset_api.py. */
const FINISHED_DATASET_STATUSES = ['completed', 'completed_with_failures', 'no_valid_chunks'];

function isAbortError(e: unknown): boolean {
  if (e instanceof DOMException && e.name === 'AbortError') return true;
  if (e instanceof Error) {
    if (e.name === 'AbortError') return true;
    if ('cause' in e && isAbortError(e.cause)) return true;
  }
  return false;
}

function formatErrorMessage(e: unknown, fallback: string): string {
  if (e instanceof HttpError || e instanceof Error) return e.message;
  return fallback;
}

function formatTransformError(e: unknown, fallback: string, timeoutMsg: string): string {
  if (isAbortError(e)) {
    return timeoutMsg;
  }
  return formatErrorMessage(e, fallback);
}

function applyRephraseResult(original: Question, rephrased: Question): Question {
  // A reworded task is both a paraphrase (edit_status) and stale for the validator.
  const merged = withTransformEditStatus(original, { ...original, ...rephrased });
  const origTask = original.task ?? '';
  const newTask = merged.task ?? '';
  if (origTask === newTask) return merged;
  return markNeedsRevalidation(merged);
}

export function DynamicImplementationPage() {
  const { t } = useTranslation(['advanced', 'common', 'nav', 'create']);
  usePageTitle('variants', 'nav');

  const auth = useAuth();
  const queryClient = useQueryClient();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [shuffleMode, setShuffleMode] = useState(false);
  const [rephraseMode, setRephraseMode] = useState(false);
  const [rephraseModelId, setRephraseModelId] = useState('');
  const [localQuestions, setLocalQuestions] = useState<Question[] | null>(null);
  const [needsRevalidation, setNeedsRevalidation] = useState(false);
  const [page, setPage] = useState(1);
  const [message, setMessage] = useState<string | null>(null);
  const [messageTone, setMessageTone] = useState<'info' | 'error' | 'success'>('info');

  const { data: datasets = [], isLoading: loadingList, isError: listError } = useQuery({
    queryKey: ['datasets'],
    queryFn: () => listDatasets(),
  });

  // Only models that answer the health probe; a choice that went down falls back to the first one.
  const { models, isLoading: loadingModels, isError: modelsError, error: modelsLoadError } = useSelectableModels();

  const activeId = selectedId ?? datasets[0]?._id ?? null;
  const activeDs = datasets.find((d) => d._id === activeId);
  // Mutation callbacks compare against this so a result that lands after the
  // user switched datasets is dropped instead of applied to the new one.
  const activeIdRef = useRef(activeId);
  activeIdRef.current = activeId;

  const { data: versions = [] } = useQuery({
    queryKey: ['dataset-versions', activeId],
    queryFn: () => listDatasetVersions(activeId!),
    enabled: !!activeId,
  });

  // Transform always branches off the tip: starting from an older version would
  // silently revert everything saved since, manual edits included.
  const version =
    activeDs?.current_version ?? versions[versions.length - 1]?.version ?? null;

  const { data: dataset, isLoading: loadingDataset, isError: datasetError, error: datasetLoadError } = useQuery({
    queryKey: ['dataset', activeId, version],
    queryFn: () => getDataset(activeId!, version ?? undefined),
    enabled: !!activeId && version != null,
  });

  // A quality revalidation of the tip version blocks transform until it ends.
  const { data: revalidation } = useQuery({
    queryKey: ['revalidation', activeId, version],
    queryFn: () => getRevalidationState(activeId!, version!),
    enabled: !!activeId && version != null,
    refetchInterval: (query) => (query.state.data?.active_run ? 5_000 : false),
  });
  const revalidating = isRevalidating(revalidation);

  const datasetStatus = dataset?.metadata?.status ?? activeDs?.metadata?.status;
  const generationFinished =
    datasetStatus != null && FINISHED_DATASET_STATUSES.includes(String(datasetStatus));

  const effectiveRephraseModelId = pickModelId(models, rephraseModelId);

  const questions = localQuestions ?? dataset?.questions ?? [];
  const numPages = Math.max(1, Math.ceil(questions.length / PAGE_SIZE));
  const start = (page - 1) * PAGE_SIZE;
  const pageQuestions = questions.slice(start, start + PAGE_SIZE);

  useEffect(() => {
    setPage((p) => Math.min(p, numPages));
  }, [numPages, activeId, version]);

  useEffect(() => {
    if (localQuestions != null) return;
    if (
      dataset?.requested_version != null
      && version != null
      && dataset.requested_version !== version
    ) {
      return;
    }
    const flagged =
      dataset?.metadata?.needs_revalidation === true
      || (dataset?.questions ?? []).some((q) => q.needs_revalidation);
    setNeedsRevalidation(flagged);
  }, [dataset, localQuestions, version]);

  // A transform preview is unsaved work that took minutes of model time — warn
  // before it is thrown away, the same way the editor does for staged edits.
  const hasPreview = localQuestions != null;
  useEffect(() => {
    if (!hasPreview) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [hasPreview]);

  const blocker = useBlocker(hasPreview);
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    if (window.confirm(t('advanced:previewUnsavedLeave'))) blocker.proceed();
    else blocker.reset();
  }, [blocker, t]);

  const transformMutation = useMutation({
    mutationFn: async ({ datasetId }: { datasetId: string }) => {
      if (!shuffleMode && !rephraseMode) throw new Error(t('advanced:chooseAtLeastOneMode'));
      let result = [...questions];

      if (rephraseMode) {
        const dsName = datasets.find((d) => d._id === datasetId)?.name ?? '';
        const resp = await rephraseQuestions(
          dsName,
          result,
          effectiveRephraseModelId || undefined,
        );
        if (resp.status !== 'success' || !resp.result) {
          const detail =
            'error' in resp && typeof resp.error === 'string' && resp.error
              ? resp.error
              : t('advanced:rephraseFailed');
          throw new Error(detail);
        }
        if (resp.result.length !== questions.length) {
          throw new Error(
            t('advanced:rephraseCountMismatch', {
              got: resp.result.length,
              expected: questions.length,
            }),
          );
        }
        result = resp.result.map((q, i) => applyRephraseResult(questions[i] ?? q, q));
      }

      if (shuffleMode) {
        result = shuffleQuestions(result);
      }

      return result;
    },
    onMutate: () => setMessage(null),
    onSuccess: (result, vars) => {
      if (vars.datasetId !== activeIdRef.current) return;
      const flagged = result.some((q) => q.needs_revalidation);
      setLocalQuestions(result);
      setNeedsRevalidation(flagged);
      setPage(1);
      setMessageTone('success');
      setMessage(
        flagged
          ? t('advanced:transformAppliedNeedsReval')
          : t('advanced:transformApplied'),
      );
    },
    onError: (e, vars) => {
      if (vars.datasetId !== activeIdRef.current) return;
      setMessageTone('error');
      setMessage(formatTransformError(e, t('advanced:transformFailed'), t('advanced:rephraseTimeout')));
    },
  });

  const saveMutation = useMutation({
    mutationFn: async ({ datasetId }: { datasetId: string }) => {
      const flagged = needsRevalidation || questions.some((q) => q.needs_revalidation);
      const normalized = questions.map((q) => {
        const req = questionToCreateRequest(q);
        req.needs_revalidation = q.needs_revalidation ?? false;
        if (q.needs_revalidation) {
          req.validation_passed = null;
          req.validation_score = null;
          req.validation_threshold = null;
          req.validation_details = null;
          req.validation_justifications = null;
        }
        return req;
      });
      const metadata = {
        ...(dataset?.metadata ?? {}),
        edited_at: new Date().toISOString(),
        edited_by: auth.username ?? 'expert',
        source_version: version,
        needs_revalidation: flagged,
      };
      return updateDataset(datasetId, { questions: normalized, metadata, base_version: version! });
    },
    onMutate: () => setMessage(null),
    onSuccess: (res, vars) => {
      queryClient.invalidateQueries({ queryKey: ['dataset', vars.datasetId] });
      queryClient.invalidateQueries({ queryKey: ['dataset-versions', vars.datasetId] });
      queryClient.invalidateQueries({ queryKey: ['datasets'] });
      if (vars.datasetId !== activeIdRef.current) return;
      setMessageTone('success');
      setMessage(t('advanced:savedAsVersion', { n: res.new_version }));
      setLocalQuestions(null);
      setNeedsRevalidation(false);
      setPage(1);
    },
    onError: (e, vars) => {
      if (vars.datasetId !== activeIdRef.current) return;
      setMessageTone('error');
      setMessage(formatErrorMessage(e, t('advanced:saveFailed')));
    },
  });

  if (loadingList) return <LoadingState label={t('advanced:loadingDatasets')} />;

  if (listError) {
    return (
      <div>
        <PageHeader title={t('advanced:variantsTitle')} subtitle={t('advanced:variantsSubtitle')} />
        <StatusBanner tone="error">{t('advanced:loadDatasetsFailed')}</StatusBanner>
      </div>
    );
  }

  if (!datasets.length) {
    return (
      <div>
        <PageHeader title={t('advanced:variantsTitle')} subtitle={t('advanced:variantsSubtitle')} />
        <EmptyState title={t('common:noData')} description={t('advanced:tryExampleHint')} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title={t('advanced:variantsTitle')}
        subtitle={t('advanced:variantsSubtitle')}
      />
      {message && (
        <div className="mt-4">
          <StatusBanner tone={messageTone === 'error' ? 'error' : messageTone === 'success' ? 'success' : 'info'}>
            {message}
          </StatusBanner>
        </div>
      )}
      {needsRevalidation && (
        <div className="mt-4">
          <StatusBanner tone="warning">
            {t('advanced:rephraseMarked')}
          </StatusBanner>
        </div>
      )}

      <div className="mt-6 space-y-4">
        <select
          className="input-field max-w-xl"
          value={activeId ?? ''}
          disabled={transformMutation.isPending || saveMutation.isPending}
          onChange={(e) => {
            setSelectedId(e.target.value);
            setLocalQuestions(null);
            setPage(1);
            setMessage(null);
          }}
        >
          {datasets.map((ds) => (
            <option key={ds._id} value={ds._id}>
              {ds.name} (v{ds.current_version})
            </option>
          ))}
        </select>

        {version != null && (
          <span className="text-sm text-slate-600">
            {t('advanced:transformsLatest', { n: version })}
          </span>
        )}
      </div>

      {loadingDataset && <div className="mt-6"><LoadingState label={t('advanced:loadingDataset')} /></div>}
      {datasetError && (
        <div className="mt-4">
          <StatusBanner tone="error">
            {formatErrorMessage(datasetLoadError, t('advanced:loadDatasetFailed'))}
          </StatusBanner>
        </div>
      )}

      {dataset && (dataset.requested_version == null || dataset.requested_version === version) && (
        <>
          <section className="mt-6 card">
            <h2 className="font-semibold">{t('advanced:chooseOptions')}</h2>
            <div className="mt-3 flex flex-wrap gap-6">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={shuffleMode} onChange={(e) => setShuffleMode(e.target.checked)} />
                {t('advanced:shuffleOptions')}
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={rephraseMode} onChange={(e) => setRephraseMode(e.target.checked)} />
                {t('advanced:rephraseQuestions')}
              </label>
            </div>
            {rephraseMode && loadingModels && (
              <p className="mt-3 text-sm text-slate-500">{t('advanced:loadingModels')}</p>
            )}
            {rephraseMode && !loadingModels && modelsError && (
              <div className="mt-3">
                <StatusBanner tone="error">
                  {formatErrorMessage(modelsLoadError, t('advanced:loadModelsFailed'))}
                </StatusBanner>
              </div>
            )}
            {rephraseMode && !loadingModels && !modelsError && models.length > 0 && (
              <div className="mt-3 max-w-xs">
                <label className="text-sm">{t('advanced:rephraseModel')}</label>
                <select
                  className="input-field mt-1"
                  value={effectiveRephraseModelId}
                  onChange={(e) => setRephraseModelId(e.target.value)}
                >
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>{m.name}</option>
                  ))}
                </select>
              </div>
            )}
            {rephraseMode && !loadingModels && !modelsError && models.length === 0 && (
              <div className="mt-3">
                <StatusBanner tone="error">{t('advanced:noModels')}</StatusBanner>
              </div>
            )}
            <button
              type="button"
              className="btn-primary mt-4"
              disabled={
                transformMutation.isPending
                || !generationFinished
                || revalidating
                || (!shuffleMode && !rephraseMode)
                || (rephraseMode && (loadingModels || modelsError || models.length === 0))
              }
              onClick={() => activeId && transformMutation.mutate({ datasetId: activeId })}
            >
              {transformMutation.isPending
                ? t('common:loading')
                : t('advanced:createPreview')}
            </button>
            {!generationFinished && (
              <div className="mt-3">
                <StatusBanner tone="warning">{t('advanced:transformNeedsFinished')}</StatusBanner>
              </div>
            )}
            {generationFinished && revalidating && (
              <div className="mt-3">
                <StatusBanner tone="warning">{t('advanced:transformBlockedByRevalidation')}</StatusBanner>
              </div>
            )}
            {generationFinished && (!shuffleMode && !rephraseMode) && (
              <p className="mt-2 text-xs text-slate-500">{t('advanced:goDisabledHint')}</p>
            )}
          </section>

          {questions.length > 0 && (
            <section className="mt-8">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="font-semibold">{t('advanced:questions')}</h2>
                <div className="flex gap-2 text-sm">
                  <button type="button" className="btn-secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>{t('common:prev')}</button>
                  <span>{page}/{numPages}</span>
                  <button type="button" className="btn-secondary" disabled={page >= numPages} onClick={() => setPage((p) => p + 1)}>{t('common:next')}</button>
                </div>
              </div>

              {pageQuestions.map((q, i) => {
                const entries = optionsEntries(q.options);
                return (
                <div key={q.question_id ?? start + i} className="card mb-4 text-sm">
                  <h4 className="font-medium">
                    {t('advanced:questionNChunk', { n: start + i + 1, chunk: q.chunk_id ?? '—' })}
                    {q.needs_revalidation && (
                      <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">{t('advanced:needsRevalidation')}</span>
                    )}
                  </h4>
                  <p><strong>{t('advanced:typeLabel')}:</strong> {questionTypeLabel(q.question_type, t)}</p>
                  <p><strong>{t('advanced:sensitivityLabel')}:</strong> {q.sensitivity_level ?? q.provocativeness ?? '—'}</p>
                  <p><strong>{t('advanced:difficultyLabel')}:</strong> {q.difficulty_level ?? q.difficulty ?? '—'}</p>
                  <p><strong>{t('advanced:taskLabel')}:</strong> {q.task}</p>
                  {entries.length > 0 ? (
                    <ul className="mt-2 space-y-1 rounded bg-slate-50 p-2 font-sans text-sm">
                      {entries.map((opt) => {
                        const correct = isCorrectOption(q.correct_answer, opt.key, opt.index);
                        return (
                          <li
                            key={opt.key}
                            className={`rounded px-2 py-1 ${correct ? 'bg-emerald-100 font-medium text-emerald-900' : 'text-slate-700'}`}
                          >
                            {opt.index}. {opt.text}
                          </li>
                        );
                      })}
                    </ul>
                  ) : (
                    <pre className="mt-1 whitespace-pre-wrap rounded bg-slate-50 p-2 font-sans text-sm">{normOptions(q.options)}</pre>
                  )}
                  <p className="mt-2"><strong>{t('advanced:correctLabel')}:</strong> {q.correct_answer}</p>
                  {q.source_chunk && (
                    <details className="mt-2 text-xs">
                      <summary className="cursor-pointer text-gena-primary">{t('advanced:sourceChunk')}</summary>
                      <div className="mt-1 flex justify-end">
                        <button
                          type="button"
                          className="text-gena-primary hover:underline"
                          onClick={() => void navigator.clipboard.writeText(q.source_chunk ?? '')}
                        >
                          {t('advanced:copyToClipboard')}
                        </button>
                      </div>
                      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2">{q.source_chunk}</pre>
                    </details>
                  )}
                </div>
              );})}

              <button
                type="button"
                className="btn-primary"
                disabled={saveMutation.isPending || revalidating}
                onClick={() => activeId && saveMutation.mutate({ datasetId: activeId })}
              >
                {saveMutation.isPending
                  ? t('advanced:saving')
                  : localQuestions
                    ? t('advanced:saveChangesAsVersion')
                    : t('advanced:saveNewVersion')}
              </button>
            </section>
          )}
        </>
      )}
    </div>
  );
}
