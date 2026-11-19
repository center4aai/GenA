import { useEffect, useMemo, useState } from 'react';
import { useBlocker, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getDataset,
  listDatasetChunks,
  listDatasetVersions,
  listDatasets,
  patchDatasetQuestions,
  getRevalidationRunItems,
  getRevalidationState,
  retryRevalidation,
  startRevalidation,
} from '@/shared/api/dataset';
import type { DatasetDetail, Question, RevalidationScope } from '@/shared/types/documents';
import { isExpert, useAuth } from '@/features/auth/authStore';
import { datasetEditorStatus, isDatasetVisibleInEditor } from '@/shared/lib/datasetVisibility';
import { compareTimestamps } from '@/shared/lib/dateTime';
import { formatUserError } from '@/shared/lib/errorMessages';
import { buildExtendedExportRows, buildResultsExportRows, downloadCsv } from '@/shared/lib/extendedExport';
import { buildRevalidationRunRows } from '@/shared/lib/revalidationExport';
import { useSelectableModels } from '@/shared/api/useSelectableModels';
import { buildGiftExport, downloadGift } from '@/shared/lib/giftExport';
import { parseOptionsText } from '@/shared/lib/optionsFormat';
import { draftFromQuestion, draftIsDirty, draftToEdit, type QuestionDraft } from '@/shared/lib/questionDraft';
import { buildQuestionPatches, exportTogglePatch, isExcludedFromExport } from '@/shared/lib/questionEdits';
import { applyQuestionEdits, ensureQids } from '@/shared/lib/questionFields';
import {
  applyQuestionFilters,
  chunkFilterOptions,
  emptyFilters,
  indexQuestions,
  type EditingFilter,
  type QuestionFilterState,
} from '@/shared/lib/questionFilters';
import { computeQuestionStats } from '@/shared/lib/questionStats';
import { omitKey } from '@/shared/lib/records';
import { shuffleQuestions } from '@/shared/lib/shuffleOptions';
import { latestVersion, versionByNumber, versionOptionLabel } from '@/shared/lib/versionLabel';
import { editNeedsRevalidation } from '@/shared/lib/revalidation';
import { isRevalidating } from '@/shared/lib/revalidationState';
import { downloadXlsx } from '@/shared/lib/xlsx';
import { usePageTitle } from '@/shared/i18n/usePageTitle';
import { PageHeader } from '@/shared/ui/PageHeader';
import { EmptyState, LoadingState } from '@/shared/ui/StatusBanner';
import { DatasetCard } from './DatasetCard';
import { RevalidationAccordion } from './RevalidationAccordion';
import { DatasetsTable } from './DatasetsTable';
import { PrintSheet, printQuestionSheet } from './PrintSheet';
import { QuestionCard } from './QuestionCard';
import { QuestionsSection } from './QuestionsSection';

const DEFAULT_PAGE_SIZE = 10;

type SaveStatus = { status: 'saving' } | { status: 'saved' } | { status: 'error'; message: string };

export function DatasetEditorPage() {
  const { t } = useTranslation(['results', 'common', 'nav', 'errors', 'advanced', 'create']);
  usePageTitle('results', 'nav');
  const auth = useAuth();
  const expert = isExpert(auth.role);
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [mineOnly, setMineOnly] = useState(!expert);
  const [dsSearch, setDsSearch] = useState('');
  const [pinnedFallbackId, setPinnedFallbackId] = useState<string | null>(null);
  // Per-question form state, keyed by question_id. Only dirty drafts are kept,
  // so `Object.keys(drafts)` is exactly the set of questions with unsaved edits.
  const [drafts, setDrafts] = useState<Record<string, QuestionDraft>>({});
  const [saveState, setSaveState] = useState<Record<string, SaveStatus>>({});
  const [togglePending, setTogglePending] = useState<Set<string>>(() => new Set());
  const [pendingFilters, setPendingFilters] = useState<QuestionFilterState>(emptyFilters);
  const [appliedFilters, setAppliedFilters] = useState<QuestionFilterState>(emptyFilters);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [shuffleOnExport, setShuffleOnExport] = useState(false);
  const [exportMsg, setExportMsg] = useState<string | null>(null);

  // ── datasets ──
  const { data: datasetsRaw = [], isLoading: loadingList, isError: listError } = useQuery({
    queryKey: ['datasets', mineOnly ? 'mine' : 'all'],
    queryFn: () => listDatasets({ mine: mineOnly }),
  });

  // Sets whose generation failed critically cannot be fixed here, so they are not listed.
  const datasets = useMemo(
    () =>
      datasetsRaw
        .filter(isDatasetVisibleInEditor)
        .sort((a, b) => compareTimestamps(b.created_at, a.created_at)),
    [datasetsRaw],
  );

  useEffect(() => {
    const param = searchParams.get('dataset');
    // Adopt the URL parameter once; re-running on every list refresh yanked the
    // selection back while drafts keyed to another dataset stayed staged.
    if (param && !selectedId && datasets.some((d) => d._id === param)) {
      setSelectedId(param);
    }
  }, [searchParams, datasets, selectedId]);

  // Pin the fallback so the "first dataset" does not change under the user
  // when the list reorders.
  useEffect(() => {
    if (pinnedFallbackId && datasets.some((d) => d._id === pinnedFallbackId)) return;
    setPinnedFallbackId(datasets[0]?._id ?? null);
  }, [datasets, pinnedFallbackId]);

  const activeId =
    (selectedId && datasets.some((d) => d._id === selectedId) ? selectedId : null) ??
    pinnedFallbackId ??
    datasets[0]?._id ??
    null;
  const activeDs = datasets.find((d) => d._id === activeId);

  // ── version ──
  const versionsQuery = useQuery({
    queryKey: ['dataset-versions', activeId],
    queryFn: () => listDatasetVersions(activeId!),
    enabled: !!activeId,
  });
  const versions = versionsQuery.data ?? [];

  // Default is the most recently created or edited version, so the dataset
  // query waits for the versions list instead of guessing `current_version`
  // and jumping afterwards.
  const version =
    selectedVersion ??
    (versionsQuery.isSuccess
      ? latestVersion(versions)?.version ?? activeDs?.current_version ?? null
      : versionsQuery.isError
        ? activeDs?.current_version ?? null
        : null);

  const datasetQuery = useQuery({
    queryKey: ['dataset', activeId, version],
    queryFn: () => getDataset(activeId!, version ?? undefined),
    enabled: !!activeId && version != null,
  });
  const dataset = datasetQuery.data;

  // ── quality revalidation of the selected version ──
  const revalidationKey = ['revalidation', activeId, version] as const;
  const revalidationQuery = useQuery({
    queryKey: revalidationKey,
    queryFn: () => getRevalidationState(activeId!, version!),
    enabled: !!activeId && version != null,
    // Poll only while a run is active; refetch the questions when it ends.
    refetchInterval: (query) => (query.state.data?.active_run ? 5_000 : false),
  });
  const revalidation = revalidationQuery.data ?? null;
  const revalidating = isRevalidating(revalidation);
  const [wasRevalidating, setWasRevalidating] = useState(false);
  useEffect(() => {
    if (revalidating) {
      setWasRevalidating(true);
    } else if (wasRevalidating) {
      setWasRevalidating(false);
      queryClient.invalidateQueries({ queryKey: ['dataset', activeId] });
    }
  }, [revalidating, wasRevalidating, queryClient, activeId]);
  const [revalidationError, setRevalidationError] = useState<string | null>(null);
  const revalidationMutation = useMutation({
    mutationFn: (
      action: { kind: 'start'; scope: RevalidationScope; modelId?: string } | { kind: 'retry' },
    ) =>
      action.kind === 'start'
        ? startRevalidation(activeId!, version!, action.scope, action.modelId)
        : retryRevalidation(activeId!, version!),
    onMutate: () => setRevalidationError(null),
    onSuccess: (state) => {
      queryClient.setQueryData(revalidationKey, state);
    },
    onError: (error) => {
      setRevalidationError(formatUserError(error, t, 'save'));
      queryClient.invalidateQueries({ queryKey: ['revalidation', activeId] });
    },
  });

  // Validators for revalidation: the same list as for a new generation.
  const { models: llmModels, allModels: allLlmModels } = useSelectableModels({ enabled: expert });
  const [downloadingRun, setDownloadingRun] = useState(false);
  async function downloadRevalidationRun(runId: string) {
    setDownloadingRun(true);
    setRevalidationError(null);
    try {
      const { items } = await getRevalidationRunItems(activeId!, version!, runId);
      downloadCsv(
        `${dataset?.name ?? 'dataset'}_v${version}_revalidation_${runId.slice(0, 8)}.csv`,
        buildRevalidationRunRows(items),
      );
    } catch (error) {
      setRevalidationError(formatUserError(error, t, 'load'));
    } finally {
      setDownloadingRun(false);
    }
  }

  const { data: chunks = [] } = useQuery({
    queryKey: ['dataset-chunks', activeId],
    queryFn: () => listDatasetChunks(activeId!, false),
    enabled: !!activeId,
  });

  // ── questions of the selected version ──
  const questions = useMemo(
    () => ensureQids(activeId ?? '', dataset?.questions ?? []),
    [activeId, dataset?.questions],
  );
  const stats = useMemo(() => computeQuestionStats(questions), [questions]);
  const chunkOptions = useMemo(() => chunkFilterOptions(questions), [questions]);
  const unsavedIds = useMemo(() => new Set(Object.keys(drafts)), [drafts]);
  const filtered = useMemo(
    () => applyQuestionFilters(indexQuestions(questions), appliedFilters, { unsavedIds }),
    [questions, appliedFilters, unsavedIds],
  );

  const effectivePageSize = pageSize === 0 ? Math.max(filtered.length, 1) : pageSize;
  const totalPages = Math.max(1, Math.ceil(filtered.length / effectivePageSize));
  const safePage = Math.min(page, totalPages);
  const pageItems = filtered.slice((safePage - 1) * effectivePageSize, safePage * effectivePageSize);

  useEffect(() => {
    setPage((p) => Math.min(p, totalPages));
  }, [totalPages]);

  // ── unsaved-work protection ──
  const unsavedCount = unsavedIds.size;
  useEffect(() => {
    if (!unsavedCount) return undefined;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [unsavedCount]);

  const blocker = useBlocker(unsavedCount > 0);
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    if (window.confirm(t('results:unsavedLeave'))) blocker.proceed();
    else blocker.reset();
  }, [blocker, t]);

  function confirmDiscard(): boolean {
    return unsavedCount === 0 || window.confirm(t('results:unsavedLeave'));
  }

  function resetContext() {
    setDrafts({});
    setSaveState({});
    setPendingFilters(emptyFilters());
    setAppliedFilters(emptyFilters());
    setPage(1);
    setExportMsg(null);
  }

  function selectDataset(id: string) {
    if (id === activeId || !confirmDiscard()) return;
    setSelectedId(id);
    setSelectedVersion(null);
    resetContext();
  }

  function selectVersion(n: number) {
    if (n === version || !confirmDiscard()) return;
    setSelectedVersion(n);
    resetContext();
  }

  function changeMineOnly(value: boolean) {
    if (value === mineOnly || !confirmDiscard()) return;
    setMineOnly(value);
    setSelectedId(null);
    setPinnedFallbackId(null);
    setSelectedVersion(null);
    resetContext();
  }

  // ── drafts ──
  function updateDraft(q: Question, patch: Partial<QuestionDraft>) {
    const qid = q.question_id;
    if (!qid) return;
    setSaveState((prev) => omitKey(prev, qid));
    setDrafts((prev) => {
      const next = { ...(prev[qid] ?? draftFromQuestion(q)), ...patch };
      return draftIsDirty(next, q) ? { ...prev, [qid]: next } : omitKey(prev, qid);
    });
  }

  function cancelDraft(qid: string) {
    setDrafts((prev) => omitKey(prev, qid));
    setSaveState((prev) => omitKey(prev, qid));
  }

  const datasetKey = ['dataset', activeId, version];

  // Manual edits are saved into the version being viewed — only transform
  // makes a new version — and one question at a time.
  const saveMutation = useMutation({
    mutationFn: async ({ q, draft }: { q: Question; draft: QuestionDraft }) => {
      const qid = String(q.question_id);
      const edit = draftToEdit(draft, q);
      const patches = buildQuestionPatches([q], { [qid]: edit });
      if (!patches.length) throw new Error(t('results:saveMatchedNothing'));
      const response = await patchDatasetQuestions(activeId!, version!, patches);
      return { response, edit };
    },
    onMutate: ({ q }) => {
      setSaveState((prev) => ({ ...prev, [String(q.question_id)]: { status: 'saving' } }));
    },
    onSuccess: ({ edit }, { q }) => {
      const qid = String(q.question_id);
      // Show the saved values at once; the refetch below brings the server's
      // own stamps (edit_status, needs_revalidation) shortly after.
      queryClient.setQueryData<DatasetDetail>(datasetKey, (old) =>
        old
          ? {
              ...old,
              questions: old.questions?.map((x) =>
                x.question_id === qid
                  ? {
                      ...applyQuestionEdits(x, edit),
                      edit_status: 'user_edited',
                      ...(editNeedsRevalidation(edit)
                        ? { validation_is_current: false, needs_revalidation: true }
                        : {}),
                    }
                  : x,
              ),
            }
          : old,
      );
      setDrafts((prev) => omitKey(prev, qid));
      setSaveState((prev) => ({ ...prev, [qid]: { status: 'saved' } }));
      queryClient.invalidateQueries({ queryKey: ['dataset', activeId] });
      queryClient.invalidateQueries({ queryKey: ['dataset-versions', activeId] });
      queryClient.invalidateQueries({ queryKey: ['datasets'] });
      queryClient.invalidateQueries({ queryKey: ['revalidation', activeId] });
    },
    onError: (error, { q }) => {
      setSaveState((prev) => ({
        ...prev,
        [String(q.question_id)]: { status: 'error', message: formatUserError(error, t, 'save') },
      }));
    },
  });

  // The include-in-export switch saves immediately and never becomes an
  // unsaved edit; the cache is updated optimistically and rolled back on error.
  const toggleMutation = useMutation({
    mutationFn: async ({ q, included }: { q: Question; included: boolean }) => {
      const patch = exportTogglePatch(q, !included);
      if (!patch) throw new Error(t('results:noQuestionId'));
      return patchDatasetQuestions(activeId!, version!, [patch]);
    },
    onMutate: async ({ q, included }) => {
      const qid = String(q.question_id);
      setTogglePending((prev) => new Set(prev).add(qid));
      await queryClient.cancelQueries({ queryKey: datasetKey });
      const previous = queryClient.getQueryData<DatasetDetail>(datasetKey);
      queryClient.setQueryData<DatasetDetail>(datasetKey, (old) =>
        old
          ? {
              ...old,
              questions: old.questions?.map((x) =>
                x.question_id === qid ? { ...x, excluded_from_export: !included } : x,
              ),
            }
          : old,
      );
      return { previous, key: datasetKey };
    },
    onError: (error, { q }, context) => {
      if (context?.previous) queryClient.setQueryData(context.key, context.previous);
      setSaveState((prev) => ({
        ...prev,
        [String(q.question_id)]: { status: 'error', message: formatUserError(error, t, 'save') },
      }));
    },
    onSettled: (_result, _error, { q }) => {
      setTogglePending((prev) => {
        const next = new Set(prev);
        next.delete(String(q.question_id));
        return next;
      });
      queryClient.invalidateQueries({ queryKey: ['dataset', activeId] });
    },
  });

  // ── filters & paging ──
  function applyFilters() {
    setAppliedFilters(pendingFilters);
    setPage(1);
  }

  function resetFilters() {
    const empty = emptyFilters();
    setPendingFilters(empty);
    setAppliedFilters(empty);
    setPage(1);
  }

  /** Replace every filter with a single editing-status bucket (the clickable hints). */
  function showOnly(bucket: EditingFilter) {
    const only: QuestionFilterState = { ...emptyFilters(), editing: new Set<EditingFilter>([bucket]) };
    setPendingFilters(only);
    setAppliedFilters(only);
    setPage(1);
  }
  const showUnsaved = () => showOnly('unsaved');
  const showExcluded = () => showOnly('excluded');

  // ── exports (server state of the selected version) ──
  const exportable = useMemo(() => {
    let qs = questions
      .filter((q) => !isExcludedFromExport(q))
      .map((q) => (typeof q.options === 'string' ? { ...q, options: parseOptionsText(q.options) } : q));
    if (shuffleOnExport) qs = shuffleQuestions(qs);
    return qs;
  }, [questions, shuffleOnExport]);

  function guardEmpty(): boolean {
    setExportMsg(null);
    if (!exportable.length) {
      setExportMsg(t('results:emptyExport'));
      return false;
    }
    return true;
  }

  async function exportResultsXlsx() {
    if (!guardEmpty()) return;
    await downloadXlsx(`${dataset?.name ?? 'dataset'}_v${version}.xlsx`, buildResultsExportRows(exportable, dataset?.metadata?.validation_model_id), 'Results');
  }

  function exportGift() {
    if (!guardEmpty()) return;
    downloadGift(`${dataset?.name ?? 'dataset'}_v${version}.gift`, buildGiftExport(exportable, dataset?.name));
  }

  async function exportPipelineXlsx() {
    if (!guardEmpty()) return;
    await downloadXlsx(
      `${dataset?.name ?? 'dataset'}_v${version}_pipeline.xlsx`,
      buildExtendedExportRows(exportable, chunks, dataset?.metadata?.validation_model_id),
      'Pipeline',
    );
  }

  function printSheet() {
    if (!guardEmpty()) return;
    printQuestionSheet();
  }

  // ── render ──
  if (loadingList) return <LoadingState label={t('common:loading')} />;
  if (listError) {
    return (
      <div>
        <PageHeader title={t('results:title')} subtitle={t('results:couldNotLoad')} />
      </div>
    );
  }
  if (!datasets.length) {
    return (
      <div>
        <PageHeader title={t('results:title')} subtitle={t('results:subtitle')} />
        <EmptyState title={t('results:noDatasets')} />
      </div>
    );
  }

  const readOnly = !expert;
  const setReady = activeDs ? datasetEditorStatus(activeDs) === 'completed' : false;
  const selectedVersionInfo = versionByNumber(versions, version);

  return (
    <div className="space-y-8">
      <PageHeader title={t('results:title')} subtitle={t('results:subtitle')} />

      <DatasetsTable
        datasets={datasets}
        activeId={activeId}
        onSelect={selectDataset}
        mineOnly={mineOnly}
        onMineOnlyChange={changeMineOnly}
        search={dsSearch}
        onSearchChange={setDsSearch}
      />

      {(datasetQuery.isLoading || (!!activeId && versionsQuery.isLoading)) && (
        <LoadingState label={t('common:loading')} />
      )}

      {dataset && (
        <>
          <DatasetCard
            dataset={dataset}
            listItem={activeDs}
            versions={versions}
            version={version}
            onVersionChange={selectVersion}
            stats={stats}
            toolbar={{
              onShowExcluded: showExcluded,
              shuffle: shuffleOnExport,
              onShuffleChange: setShuffleOnExport,
              onExcel: () => void exportResultsXlsx(),
              onGift: exportGift,
              onPrint: printSheet,
              onExtended: () => void exportPipelineXlsx(),
              message: exportMsg,
            }}
            revalidation={
              revalidation && setReady && revalidation.available && revalidation.total_count > 0 ? (
                <RevalidationAccordion
                  key={`${activeId}:${version}`}
                  state={revalidation}
                  versionLabel={
                    selectedVersionInfo
                      ? versionOptionLabel(selectedVersionInfo, t)
                      : t('results:version', { n: version })
                  }
                  unsavedCount={unsavedCount}
                  canStart={expert}
                  starting={revalidationMutation.isPending}
                  error={revalidationError}
                  models={llmModels}
                  labelModels={allLlmModels}
                  defaultModelId={dataset.metadata?.validation_model_id}
                  onStart={(scope, modelId) => revalidationMutation.mutate({ kind: 'start', scope, modelId })}
                  onRetry={() => revalidationMutation.mutate({ kind: 'retry' })}
                  onDownloadRun={(runId) => void downloadRevalidationRun(runId)}
                  downloading={downloadingRun}
                />
              ) : null
            }
          />

          <QuestionsSection
            total={filtered.length}
            totalUnfiltered={questions.length}
            unsavedCount={unsavedCount}
            onShowUnsaved={showUnsaved}
            pendingFilters={pendingFilters}
            appliedFilters={appliedFilters}
            chunkOptions={chunkOptions}
            onPendingFiltersChange={setPendingFilters}
            onApplyFilters={applyFilters}
            onResetFilters={resetFilters}
            page={safePage}
            totalPages={totalPages}
            pageSize={pageSize}
            onPageChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
            }}
            readOnly={readOnly}
            lockedMessage={revalidating ? t('results:revalEditingLocked') : null}
          >
            {pageItems.map(({ q, index }) => {
              const qid = String(q.question_id);
              const state = saveState[qid];
              return (
                <QuestionCard
                  key={qid}
                  question={q}
                  index={index}
                  draft={drafts[qid]}
                  readOnly={readOnly}
                  editLocked={revalidating}
                  saving={state?.status === 'saving'}
                  saved={state?.status === 'saved'}
                  saveError={state?.status === 'error' ? state.message : null}
                  exportPending={togglePending.has(qid)}
                  onDraftChange={(patch) => updateDraft(q, patch)}
                  onSave={() => {
                    const draft = drafts[qid];
                    if (draft) saveMutation.mutate({ q, draft });
                  }}
                  onCancel={() => cancelDraft(qid)}
                  onToggleExport={(included) => toggleMutation.mutate({ q, included })}
                />
              );
            })}
          </QuestionsSection>

          <PrintSheet datasetName={dataset.name} version={version} questions={exportable} />
        </>
      )}
    </div>
  );
}
