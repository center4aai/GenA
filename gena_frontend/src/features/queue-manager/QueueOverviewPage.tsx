import { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listQueues,
  listDatasets,
  getTask,
  listQueueTasks,
  listDatasetChunks,
  getPipelineStats,
  deleteQueue,
  retryFailedTasks,
  cancelTask,
} from '@/shared/api/dataset';
import { listModelsHealth } from '@/shared/api/agent';
import {
  recountQueueAggregates,
  queueStatsFromQueue,
  queueStatsFromByStatus,
  computeQueuePipeline,
  pipelineFromStats,
  tasksForStage,
} from '@/shared/lib/progress';
import {
  chunkBlockType,
  chunkPageRange,
  chunkPreviewText,
  storedChunksToDetailed,
} from '@/shared/lib/normalizeChunk';
import { isExpert, useAuth } from '@/features/auth/authStore';
import { AUTH_ENABLED } from '@/shared/config/auth';
import { usePageTitle } from '@/shared/i18n/usePageTitle';
import { formatTimestamp } from '@/shared/lib/dateTime';
import { humanQueueName } from '@/shared/lib/labels';
import { taskMessageLabel, gateReasonLabel } from '@/shared/lib/taskMessage';
import { useSort } from '@/shared/lib/useSort';
import { useResizableColumns } from '@/shared/lib/useResizableColumns';
import { PageHeader } from '@/shared/ui/PageHeader';
import { MetricCard } from '@/shared/ui/MetricCard';
import { PipelinePhaseStepper } from '@/shared/ui/PipelinePhaseStepper';
import { CollapsibleSection } from '@/shared/ui/CollapsibleSection';
import { Pagination } from '@/shared/ui/Pagination';
import { SortableTh } from '@/shared/ui/SortableTh';
import { SegmentedProgressBar, type ProgressSegment } from '@/shared/ui/charts';
import { StatusBanner, LoadingState, EmptyState } from '@/shared/ui/StatusBanner';
import type { ChunkRecord, Dataset, PipelineStats, QueueStats, Task } from '@/shared/types/documents';

/** Chunker block types shown as a chip on chunk previews (body is implicit). */
const BLOCK_TYPE_LABEL_KEYS: Record<string, string> = {
  inset: 'progress:blockType.inset',
  caption: 'progress:blockType.caption',
  glossary_entry: 'progress:blockType.glossary_entry',
  list: 'progress:blockType.list',
};

/** What the chunker threw away, keyed like chunker/blocks.py DROPPED_BLOCK_TYPES. */
const DROPPED_BLOCK_LABEL_KEYS: Record<string, string> = {
  exercise: 'progress:droppedType.exercise',
  front_matter: 'progress:droppedType.front_matter',
  toc: 'progress:droppedType.toc',
  imprint: 'progress:droppedType.imprint',
  abbreviations: 'progress:droppedType.abbreviations',
  caption_short: 'progress:droppedType.caption_short',
  figure_text: 'progress:droppedType.figure_text',
  debris: 'progress:droppedType.debris',
  junk: 'progress:droppedType.junk',
  table: 'progress:droppedType.table',
  inset_short: 'progress:droppedType.inset_short',
};

type ProgressT = (key: string, opts?: Record<string, unknown>) => string;

const POLL_MS = 5_000;
const TASK_PAGE_SIZE = 25;
const FAILED_PREVIEW_LIMIT = 100;
// Only the legacy path below still bulk-loads tasks: a queue with no dataset
// behind it has no pipeline-stats to read, and such queues are small.
const TASK_FETCH_LIMIT = 5000;
const CHUNK_PREVIEW_LIMIT = 50;
const STATUS_FILTERS = ['All', 'pending', 'processing', 'completed', 'failed', 'cancelled'];

// Table column key -> the task field the server sorts on. The two label columns
// ("Тип", "Ошибка") are rendered through i18n, so no server-side sort can match
// their alphabetical order; sorting by the field behind them groups like with
// like, which is what the columns are used for.
const TASK_SORT_FIELDS: Record<string, string> = {
  type: 'task_type',
  chunk: 'chunk_id',
  status: 'status',
  gen: 'generation_model_id',
  val: 'validation_model_id',
  updated: 'updated_at',
  error: 'message_code',
};

/** Server total for the status the table is currently filtered to. */
function totalForFilter(stats: QueueStats, statusFilter: string): number {
  switch (statusFilter) {
    case 'pending': return stats.pendingCount;
    case 'processing': return stats.processingCount;
    case 'completed': return stats.completedCount;
    case 'failed': return stats.failedCount;
    case 'cancelled': return stats.cancelledCount;
    default: return stats.taskCount;
  }
}

function taskTypeLabel(task: Task, t: ProgressT): string {
  if (task.task_type === 'chunk') return t('progress:stageChunking');
  if (task.task_type === 'gate') return t('progress:stageGate');
  const qt = (task.question_type ?? '').toLowerCase();
  if (qt === 'multi' || qt === 'multiple' || qt === 'multiple_choice') {
    return t('progress:stageGenerationMulti');
  }
  if (qt === 'open' || qt === 'open_question') {
    return t('progress:stageGenerationOpen');
  }
  return t('progress:stageGenerationOne');
}

function shortQuestionTypeLabel(qt: string, t: ProgressT): string {
  const key = qt.trim().toLowerCase();
  if (key === 'multi' || key === 'multiple' || key === 'multiple_choice') {
    return t('progress:qtypeShortMulti');
  }
  if (key === 'open' || key === 'open_question') {
    return t('progress:qtypeShortOpen');
  }
  if (key === 'one' || key === 'one_choice' || key === 'single') {
    return t('progress:qtypeShortOne');
  }
  return qt;
}

function pipelineModeLabel(mode: string | undefined, t: ProgressT): string {
  if (!mode) return '';
  if (mode === 'full') return t('progress:pipelineModeFull');
  if (mode === 'generator_validator_gate') return t('progress:pipelineModeNoRefine');
  if (mode === 'generator_validator') return t('progress:pipelineModeNoGateNoRefine');
  return mode;
}

function queueSegments(t: ProgressT, stats: QueueStats): ProgressSegment[] {
  return [
    { label: t('progress:completed'), value: stats.completedCount, colorClass: 'bg-emerald-600' },
    { label: t('progress:processing'), value: stats.processingCount, colorClass: 'bg-teal-500' },
    { label: t('progress:pending'), value: stats.pendingCount, colorClass: 'bg-stone-300' },
    { label: t('progress:failed'), value: stats.failedCount, colorClass: 'bg-red-500' },
    { label: t('progress:cancelled'), value: stats.cancelledCount, colorClass: 'bg-amber-400' },
  ];
}

function statusFilterLabel(t: ProgressT, status: string): string {
  if (status === 'All') return t('progress:filterAll');
  return t(`progress:${status}`);
}

function pct(n: number, d: number): string {
  if (!d) return '—';
  return `${Math.round((n / d) * 1000) / 10}%`;
}

function fmtConf(v: number | null | undefined): string {
  if (v == null) return '—';
  return v.toFixed(2);
}

export function QueueOverviewPage() {
  const { t } = useTranslation(['progress', 'common', 'nav']);
  usePageTitle('progress', 'nav');
  const auth = useAuth();
  const canRunExpertActions = !AUTH_ENABLED || isExpert(auth.role);

  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const queueName = searchParams.get('queue');

  const [statusFilter, setStatusFilter] = useState('All');
  const [page, setPage] = useState(1);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [retryMsg, setRetryMsg] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [loadAllChunks, setLoadAllChunks] = useState(false);
  const [loadValidChunks, setLoadValidChunks] = useState(false);

  // The table is sorted and paged by the server, so both live above the query
  // that reads them. `useSort` keeps owning the header state; only `sortItems`
  // is gone, because a client sort would reorder the current page alone.
  const taskSort = useSort<Task>();
  const requestTaskSort = (key: string) => {
    taskSort.requestSort(key);
    setPage(1);
  };
  const taskCols = useResizableColumns({ gen: 170, val: 170, error: 280 });

  const { data: queues = [], isLoading: loadingQueues, isFetching: fetchingQueues } = useQuery({
    queryKey: ['queues'],
    queryFn: listQueues,
    refetchInterval: autoRefresh ? POLL_MS : false,
  });
  const queue = queues.find((q) => q.name === queueName) ?? null;
  const queueExists = !!queue;
  const queueHasActiveWork =
    (queue?.pending_count ?? 0) > 0 || (queue?.processing_count ?? 0) > 0;

  const { data: health = [] } = useQuery({
    queryKey: ['models-health'],
    queryFn: listModelsHealth,
    refetchInterval: autoRefresh ? POLL_MS : false,
  });
  const healthMap = useMemo(() => Object.fromEntries(health.map((h) => [h.id, h])), [health]);

  const { data: datasets = [] } = useQuery({
    queryKey: ['datasets'],
    queryFn: () => listDatasets(),
    refetchInterval: autoRefresh ? POLL_MS : false,
  });

  const queueDataset = useMemo(
    () => (datasets as Dataset[]).find((d) => d.metadata?.queue_name === queueName) ?? null,
    [datasets, queueName],
  );
  const datasetId = queueDataset?._id;

  const { data: pipelineStats } = useQuery({
    queryKey: ['pipeline-stats', datasetId],
    queryFn: () => getPipelineStats(datasetId!),
    enabled: !!datasetId,
    refetchInterval: autoRefresh ? POLL_MS : false,
  });

  // Legacy path: a queue with no dataset has no pipeline-stats, so its phases
  // still have to be counted from the tasks themselves.
  const { data: allQueueTasks = [] } = useQuery({
    queryKey: ['queue-tasks-all', queueName],
    queryFn: () => listQueueTasks(queueName!, undefined, TASK_FETCH_LIMIT, true),
    enabled: queueExists && !datasetId,
    refetchInterval: autoRefresh && queueHasActiveWork ? POLL_MS : false,
  });

  const { data: openTaskDetail } = useQuery({
    queryKey: ['task-detail', openTaskId],
    queryFn: () => getTask(openTaskId!),
    enabled: !!openTaskId,
  });

  // Counts come from the queue document, where the server has already grouped
  // every task by status. Recounting them from a fetched page would only ever
  // describe that page.
  const stats = useMemo(() => queueStatsFromQueue(queue), [queue]);

  const apiStatusFilter = statusFilter === 'All' ? undefined : statusFilter;
  const sortBy = taskSort.sort ? TASK_SORT_FIELDS[taskSort.sort.key] : undefined;
  const sortDir = taskSort.sort?.dir;
  const { data: queueTasks = [] } = useQuery({
    queryKey: ['queue-tasks', queueName, apiStatusFilter, page, sortBy, sortDir],
    queryFn: () =>
      listQueueTasks(queueName!, apiStatusFilter, TASK_PAGE_SIZE, true, {
        skip: (page - 1) * TASK_PAGE_SIZE,
        sortBy,
        sortDir,
      }),
    enabled: queueExists,
    placeholderData: (prev) => prev,
    refetchInterval: autoRefresh && queueHasActiveWork ? POLL_MS : false,
  });

  const { data: failedTasks = [] } = useQuery({
    queryKey: ['queue-tasks-failed', queueName],
    queryFn: () => listQueueTasks(queueName!, 'failed', FAILED_PREVIEW_LIMIT, true),
    enabled: queueExists && stats.failedCount > 0,
    refetchInterval: autoRefresh && queueHasActiveWork ? POLL_MS : false,
  });

  const pipelinePhases = useMemo(
    () => (pipelineStats
      ? pipelineFromStats(pipelineStats)
      : computeQueuePipeline(allQueueTasks, queueDataset?.metadata)),
    [pipelineStats, allQueueTasks, queueDataset],
  );

  const { data: allChunks = [], isFetching: loadingAllChunks } = useQuery({
    queryKey: ['dataset-chunks-all', datasetId],
    queryFn: () => listDatasetChunks(datasetId!, false),
    enabled: !!datasetId && loadAllChunks,
  });

  const { data: validChunks = [], isFetching: loadingValidChunks } = useQuery({
    queryKey: ['dataset-chunks-valid', datasetId],
    queryFn: () => listDatasetChunks(datasetId!, true),
    enabled: !!datasetId && loadValidChunks,
  });

  const genStats = useMemo(
    () => (pipelineStats
      ? queueStatsFromByStatus(pipelineStats.generation.by_status)
      : recountQueueAggregates(tasksForStage(allQueueTasks, 'generation'))),
    [pipelineStats, allQueueTasks],
  );

  // Отсеянные фрагменты: только те, у которых есть вердикт гейта. Непроверенные
  // тоже лежат с gate_passed=false, но причины отсева у них нет.
  const rejectedChunks = useMemo(
    () => allChunks.filter((c) => c.gate_passed === false && !!c.gate_result),
    [allChunks],
  );

  const chunkPhase = pipelinePhases.find((p) => p.key === 'chunking');
  const gatePhase = pipelinePhases.find((p) => p.key === 'gate');
  const genPhase = pipelinePhases.find((p) => p.key === 'generation');

  // The stepper, the section header and the questions card all print this one
  // pair. They used to compute it three different ways and disagree: one
  // counted failed tasks as progress, another measured against however many
  // tasks had been fetched.
  const questionsDone = genPhase?.completed ?? genStats.completedCount;
  const questionsTotal = genPhase?.total || genStats.taskCount;

  const totalChunks =
    pipelineStats?.chunking.total_chunks
    ?? queueDataset?.metadata?.total_chunks
    ?? chunkPhase?.total
    ?? 0;
  const chunksPassed =
    pipelineStats?.gate.passed
    ?? queueDataset?.metadata?.chunks_passed_gate
    ?? 0;
  const chunksRejected =
    pipelineStats?.gate.rejected
    ?? queueDataset?.metadata?.chunks_rejected
    ?? 0;
  // Фрагменты, до которых отбор ещё не дошёл: без этого числа «прошло 10 из 50»
  // читается как «40 отсеяно».
  const chunksNotChecked = Math.max(
    0,
    (pipelineStats?.gate.total ?? totalChunks) - chunksPassed - chunksRejected,
  );
  const showChunkingSection = !!chunkPhase && chunkPhase.state === 'completed' && totalChunks > 0;
  const showGateSection =
    !!gatePhase
    && (gatePhase.state === 'completed' || gatePhase.state === 'disabled' || gatePhase.state === 'active'
      || chunksPassed > 0 || (pipelineStats?.gate.rejected ?? 0) > 0);
  const showGenerationSection =
    !!genPhase && (genPhase.total > 0 || genPhase.state === 'active' || genPhase.state === 'completed' || genPhase.state === 'failed');

  // Which models this run uses comes from pipeline-stats, which collects them
  // while counting tasks. Scanning task documents for the same answer meant the
  // banner only knew about models mentioned in the rows that were loaded.
  const downModels = useMemo(() => {
    const ids = new Set<string>();
    const fromStats = pipelineStats?.models;
    if (fromStats) {
      for (const id of [...fromStats.generation, ...fromStats.validation]) {
        if (healthMap[id] && !healthMap[id].available) ids.add(id);
      }
    } else {
      for (const task of allQueueTasks) {
        for (const id of [task.generation_model_id, task.validation_model_id]) {
          if (id && healthMap[id] && !healthMap[id].available) ids.add(id);
        }
      }
    }
    return [...ids].map((id) => healthMap[id]?.name ?? id);
  }, [pipelineStats, allQueueTasks, healthMap]);

  // Paging targets are the server's counts, so the last page is the real last
  // page rather than the end of whatever the fetch limit allowed through.
  const tableTotal = totalForFilter(stats, statusFilter);
  const totalPages = Math.max(1, Math.ceil(tableTotal / TASK_PAGE_SIZE));
  const failedTasksTruncated = stats.failedCount > failedTasks.length;


  async function handleRetry() {
    if (!queueName || retrying) return;
    setRetrying(true);
    setActionError(null);
    setRetryMsg(null);
    try {
      const res = await retryFailedTasks(queueName);
      setRetryMsg(
        t('progress:retriedFailed', {
          n: res.tasks_retried,
          name: queueName,
          datasets: res.datasets_reopened
            ? t('progress:datasetsReopened', { n: res.datasets_reopened })
            : '',
        }),
      );
      await queryClient.invalidateQueries({ queryKey: ['queues'] });
      await queryClient.invalidateQueries({ queryKey: ['queue-tasks', queueName] });
      await queryClient.invalidateQueries({ queryKey: ['queue-tasks-all', queueName] });
      await queryClient.invalidateQueries({ queryKey: ['queue-tasks-failed', queueName] });
      await queryClient.invalidateQueries({ queryKey: ['pipeline-stats', datasetId] });
    } catch (e) {
      setActionError(e instanceof Error ? e.message : t('progress:retryFailedGeneric'));
    } finally {
      setRetrying(false);
    }
  }

  async function handleCancelTask(taskId: string) {
    if (!window.confirm(t('progress:cancelTaskConfirm'))) return;
    setActionError(null);
    setRetryMsg(null);
    setCancellingId(taskId);
    try {
      await cancelTask(taskId);
      setRetryMsg(t('progress:taskCancelled'));
      await queryClient.invalidateQueries({ queryKey: ['queues'] });
      await queryClient.invalidateQueries({ queryKey: ['queue-tasks', queueName] });
      await queryClient.invalidateQueries({ queryKey: ['queue-tasks-all', queueName] });
      await queryClient.invalidateQueries({ queryKey: ['queue-tasks-failed', queueName] });
      await queryClient.invalidateQueries({ queryKey: ['pipeline-stats', datasetId] });
    } catch (e) {
      setActionError(e instanceof Error ? e.message : t('progress:cancelFailed'));
    } finally {
      setCancellingId(null);
    }
  }

  async function handleDelete() {
    if (!queueName || deleting) return;
    if (!window.confirm(t('progress:deleteQueueConfirm', { name: queueName }))) return;
    setDeleting(true);
    setActionError(null);
    try {
      await deleteQueue(queueName);
      await queryClient.invalidateQueries({ queryKey: ['queues'] });
      navigate('/queue_manager');
    } catch (e) {
      setActionError(e instanceof Error ? e.message : t('progress:deleteFailed'));
      setDeleting(false);
    }
  }

  function modelCell(id?: string) {
    if (!id) return '—';
    const h = healthMap[id];
    if (!h) return id;
    return h.available ? h.name : `⚠ ${h.name}`;
  }

  const backLink = (
    <Link to="/queue_manager" className="text-sm text-gena-primary hover:underline">
      ← {t('progress:backToQueues')}
    </Link>
  );

  const meta = pipelineStats?.meta ?? queueDataset?.metadata;
  const pipelineMode = meta?.pipeline_mode;
  const ablation = !!meta?.ablation_testing;

  if (loadingQueues) return <LoadingState label={t('progress:loadingQueue')} />;

  if (queueName && !queueExists && fetchingQueues) {
    return <LoadingState label={t('progress:loadingQueue')} />;
  }

  if (!queueName || !queueExists) {
    return (
      <div>
        <div className="mb-3">{backLink}</div>
        <EmptyState title={t('progress:queueNotFound')} description={queueName ?? undefined} />
      </div>
    );
  }

  return (
    <div>
      <div className="mb-3">{backLink}</div>
      <PageHeader
        title={t('progress:generationFromDocument', { name: humanQueueName(queueName) })}
        subtitle={
          <span className="mt-1 block font-mono text-xs text-slate-400" title={queueName}>
            {t('progress:techName', { name: queueName })}
          </span>
        }
        actions={
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
            {t('progress:autoRefresh')}
          </label>
        }
      />

      {(pipelineMode || ablation) && (
        <div className="mt-3 flex flex-wrap gap-2">
          {pipelineMode && (
            <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-700">
              {t('progress:pipelineMode')}: {pipelineModeLabel(pipelineMode, t)}
            </span>
          )}
          {ablation && (
            <span className="rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-xs font-medium text-violet-700">
              {t('progress:ablationBadge')}
            </span>
          )}
        </div>
      )}

      {retryMsg && (
        <div className="mt-4"><StatusBanner tone="info">{retryMsg}</StatusBanner></div>
      )}
      {actionError && (
        <div className="mt-4"><StatusBanner tone="error">{actionError}</StatusBanner></div>
      )}

      <div className="mt-4 space-y-4">
        {(pipelineStats || stats.taskCount > 0) && (
          <div className="card">
            <PipelinePhaseStepper
              phases={pipelinePhases}
              labels={{
                chunking: t('progress:stepChunking'),
                gate: t('progress:stepGate'),
                generation: t('progress:stepGeneration'),
                done: t('progress:stepDone'),
                disabled: t('progress:stepDisabled'),
              }}
            />
          </div>
        )}

        <div className="space-y-2">
          {showChunkingSection && (
            <CollapsibleSection
              title={t('progress:previewAllChunks', { n: totalChunks })}
              onToggle={(open) => { if (open) setLoadAllChunks(true); }}
            >
              <ChunkPreviewList
                chunks={allChunks}
                loading={loadingAllChunks}
                emptyLabel={t('progress:noChunksYet')}
                loadingLabel={t('progress:loadingChunks')}
                t={t}
              />
            </CollapsibleSection>
          )}

          {showGateSection && (
            <CollapsibleSection
              title={
                gatePhase?.state === 'disabled'
                  ? t('progress:gateDisabledByMode')
                  : t(
                      chunksNotChecked > 0
                        ? 'progress:gateDetailsRunning'
                        : 'progress:gateDetails',
                      {
                        passed: chunksPassed,
                        rejected: chunksRejected,
                        pending: chunksNotChecked,
                        total: totalChunks || pipelineStats?.gate.total || 0,
                      },
                    )
              }
              onToggle={(open) => {
                if (open) {
                  setLoadValidChunks(true);
                  // Отсеянные фрагменты приходят только в полном списке, а причина
                  // отсева нужна именно здесь, рядом со статистикой отбора.
                  setLoadAllChunks(true);
                }
              }}
            >
              <GateDetailsBody stats={pipelineStats} t={t} />
              {gatePhase?.state !== 'disabled' && rejectedChunks.length > 0 && (
                <div className="mt-4">
                  <div className="mb-2 text-xs font-medium text-slate-600">
                    {t('progress:previewRejectedChunks', { n: rejectedChunks.length })}
                  </div>
                  <ChunkPreviewList
                    chunks={rejectedChunks}
                    loading={loadingAllChunks}
                    emptyLabel={t('progress:noChunksYet')}
                    loadingLabel={t('progress:loadingChunks')}
                    t={t}
                  />
                </div>
              )}
              {gatePhase?.state !== 'disabled' && (
                <div className="mt-4">
                  <div className="mb-2 text-xs font-medium text-slate-600">
                    {t('progress:previewValidChunks', {
                      k: chunksPassed,
                      n: totalChunks || 0,
                    })}
                  </div>
                  <ChunkPreviewList
                    chunks={validChunks}
                    loading={loadingValidChunks}
                    emptyLabel={t('progress:noChunksYet')}
                    loadingLabel={t('progress:loadingChunks')}
                    t={t}
                    passedOnly
                  />
                </div>
              )}
            </CollapsibleSection>
          )}

          {showGenerationSection && (
            <CollapsibleSection
              title={t('progress:generationDetails', {
                done: questionsDone,
                total: questionsTotal,
              })}
            >
              <GenerationDetailsBody
                genStats={genStats}
                questionsDone={questionsDone}
                questionsTotal={questionsTotal}
                pipelineStats={pipelineStats}
                t={t}
              />
            </CollapsibleSection>
          )}
        </div>

        {downModels.length > 0 && (
          <StatusBanner tone="warning">
            {t('progress:modelsSomeDown', { names: downModels.join(', ') })}
          </StatusBanner>
        )}

        {stats.failedCount > 0 && (
          <details className="rounded-xl border border-red-200 bg-red-50/50 p-4">
            <summary className="cursor-pointer font-medium text-red-800">
              {t('progress:failedTasksSummary', { count: stats.failedCount })}
            </summary>
            {failedTasksTruncated && (
              <p className="mt-2 text-xs text-red-700">
                {t('progress:failedTruncated', { shown: failedTasks.length, total: stats.failedCount })}
              </p>
            )}
            <ul className="mt-3 space-y-2 text-xs">
              {failedTasks.map((task) => {
                const msg = taskMessageLabel(task, t);
                return (
                  <li key={task._id} className="rounded border border-red-100 bg-white p-2">
                    <span className="font-mono">{task._id}</span>
                    {' · '}
                    {taskTypeLabel(task, t)}
                    {' · '}
                    {t('progress:taskChunk')} {task.chunk_id}
                    {msg ? <>{' · '}<span className="text-red-700">{msg}</span></> : null}
                  </li>
                );
              })}
            </ul>
            {canRunExpertActions && (
              <button
                type="button"
                className="btn-primary mt-3 disabled:opacity-50"
                disabled={retrying}
                onClick={() => void handleRetry()}
              >
                {retrying ? t('progress:retrying') : t('progress:restartFailed', { n: stats.failedCount })}
              </button>
            )}
          </details>
        )}

        <div className="space-y-2">
          <div>
            <h3 className="text-base font-semibold text-slate-800">{t('progress:tasksTableTitle')}</h3>
            <p className="mt-1 text-sm text-slate-500">{t('progress:tasksTableSubtitle')}</p>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <select
              className="input-field max-w-xs"
              value={statusFilter}
              onChange={(e) => { setStatusFilter(e.target.value); setPage(1); setOpenTaskId(null); }}
            >
              {STATUS_FILTERS.map((s) => (
                <option key={s} value={s}>{statusFilterLabel(t, s)}</option>
              ))}
            </select>
            <Pagination
              page={page}
              totalPages={totalPages}
              total={tableTotal}
              onPageChange={setPage}
              showInput
              countLabel={(n) => t('common:tasks', { count: n })}
            />
          </div>
        </div>

        <div className="w-full overflow-x-auto rounded-xl border border-slate-200 bg-white">
          <table className="data-table text-xs" ref={taskCols.tableRef} style={taskCols.tableStyle}>
            <thead>
              <tr>
                <SortableTh label={t('progress:colType')} sortKey="type" sort={taskSort.sort} onSort={requestTaskSort} resize={taskCols} />
                <SortableTh label={t('progress:colChunk')} sortKey="chunk" sort={taskSort.sort} onSort={requestTaskSort} resize={taskCols} />
                <SortableTh label={t('progress:colStatus')} sortKey="status" sort={taskSort.sort} onSort={requestTaskSort} resize={taskCols} />
                <SortableTh label={t('progress:colGenModel')} sortKey="gen" sort={taskSort.sort} onSort={requestTaskSort} resize={taskCols} />
                <SortableTh label={t('progress:colValModel')} sortKey="val" sort={taskSort.sort} onSort={requestTaskSort} resize={taskCols} />
                <SortableTh label={t('progress:colUpdated')} sortKey="updated" sort={taskSort.sort} onSort={requestTaskSort} resize={taskCols} />
                <SortableTh label={t('progress:colError')} sortKey="error" sort={taskSort.sort} onSort={requestTaskSort} resize={taskCols} />
              </tr>
            </thead>
            <tbody>
              {queueTasks.map((task) => {
                const msg = taskMessageLabel(task, t);
                return (
                  <tr
                    key={task._id}
                    className="cursor-pointer hover:bg-slate-50"
                    onClick={() => setOpenTaskId(openTaskId === task._id ? null : task._id)}
                  >
                    <td>{taskTypeLabel(task, t)}</td>
                    <td>{task.chunk_id}</td>
                    <td>{statusFilterLabel(t, task.status ?? '')}</td>
                    <td className="whitespace-normal break-words align-top" title={task.generation_model_id}>{modelCell(task.generation_model_id)}</td>
                    <td className="whitespace-normal break-words align-top" title={task.validation_model_id}>{modelCell(task.validation_model_id)}</td>
                    <td className="whitespace-nowrap align-top text-slate-500">{formatTimestamp(task.updated_at)}</td>
                    <td className="whitespace-normal break-words align-top text-slate-700" title={msg || task.error}>
                      {msg || '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {stats.taskCount === 0 && <EmptyState title={t('progress:emptyZero')} />}

        {openTaskId && (() => {
          // The list is fetched brief; the chunk text and the raw result come
          // from a single-task request made only when a task is opened. That
          // request is also what keeps the panel alive after paging away from
          // the row that opened it.
          const task = openTaskDetail ?? queueTasks.find((x) => x._id === openTaskId);
          if (!task) return null;
          return (
            <TaskDrillDown
              task={task}
              onCancel={task.status === 'pending' ? () => void handleCancelTask(task._id) : undefined}
              cancelling={cancellingId === task._id}
            />
          );
        })()}

        {canRunExpertActions && (
          <button
            type="button"
            className="btn-secondary text-red-700"
            disabled={deleting}
            onClick={() => void handleDelete()}
          >
            {deleting ? t('progress:deleting') : t('progress:deleteQueue')}
          </button>
        )}
      </div>
    </div>
  );
}

function GateDetailsBody({
  stats,
  t,
}: {
  stats?: PipelineStats;
  t: ProgressT;
}) {
  if (!stats) {
    return <p className="text-sm text-slate-500">{t('progress:loadingQueue')}</p>;
  }
  if (stats.gate.state === 'disabled') {
    return <p className="text-sm text-slate-600">{t('progress:gateDisabledByMode')}</p>;
  }

  const reasons = Object.entries(stats.gate.rejection_reasons).sort((a, b) => b[1] - a[1]);
  const criteria = stats.gate.criteria;
  const scored = criteria.scored || stats.gate.passed + stats.gate.rejected || 1;
  const qtypes = Object.entries(stats.gate.question_types_valid);
  const notChecked = Math.max(0, stats.gate.total - stats.gate.passed - stats.gate.rejected);
  // Only worth showing once the chunker labels more than plain body text.
  const blockTypes = Object.entries(stats.gate.by_block_type ?? {}).filter(([bt]) => bt !== 'body');
  const dropped = Object.entries(stats.meta.chunking_dropped_blocks ?? {}).filter(([, v]) => v.count > 0);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <MetricCard label={t('progress:mTotal')} value={stats.gate.total} />
        <MetricCard label={t('progress:gatePassed')} value={stats.gate.passed} tone="success" />
        <MetricCard label={t('progress:gateRejected')} value={stats.gate.rejected} tone={stats.gate.rejected ? 'danger' : 'default'} />
        <MetricCard label={t('progress:gateNotChecked')} value={notChecked} />
        <MetricCard label={t('progress:passRate')} value={pct(stats.gate.passed, stats.gate.passed + stats.gate.rejected)} />
      </div>

      {reasons.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium text-slate-600">{t('progress:rejectionReasons')}</div>
          <ul className="space-y-1 text-xs text-slate-700">
            {reasons.map(([reason, count]) => (
              <li key={reason} className="flex justify-between gap-4 rounded border border-slate-100 bg-white px-2 py-1">
                <span>{gateReasonLabel(reason, t)}</span>
                <span>{count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(blockTypes.length > 0 || dropped.length > 0) && (
        <div>
          <div className="mb-1 text-xs font-medium text-slate-600">{t('progress:gateByBlockType')}</div>
          <div className="flex flex-wrap gap-2">
            {blockTypes.map(([bt, n]) => (
              <span key={bt} className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs text-indigo-800">
                {t(BLOCK_TYPE_LABEL_KEYS[bt] ?? 'progress:blockType.other', { type: bt })}: {n.passed}/{n.total}
              </span>
            ))}
            {dropped.map(([bt, v]) => (
              <span key={`dropped-${bt}`} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                {t('progress:droppedBlock', { type: t(DROPPED_BLOCK_LABEL_KEYS[bt] ?? 'progress:blockType.other', { type: bt }), n: v.count })}
              </span>
            ))}
          </div>
        </div>
      )}

      <div>
        <div className="mb-1 text-xs font-medium text-slate-600">{t('progress:gateCriteria')}</div>
        <div className="grid gap-2 sm:grid-cols-3">
          {([
            ['c1', t('progress:criterionC1'), criteria.c1_pass, criteria.c1_avg_confidence],
            ['c2', t('progress:criterionC2'), criteria.c2_pass, criteria.c2_avg_confidence],
            ['c3', t('progress:criterionC3'), criteria.c3_pass, criteria.c3_avg_confidence],
          ] as const).map(([key, label, pass, conf]) => (
            <div key={key} className="rounded-lg border border-slate-100 bg-white p-2 text-xs">
              <div className="font-medium text-slate-800">{label}</div>
              <div className="mt-1 text-slate-600">{pass}/{scored} ({pct(pass, scored)})</div>
              <div className="text-slate-500">{t('progress:avgConfidence', { v: fmtConf(conf) })}</div>
            </div>
          ))}
        </div>
      </div>

      {qtypes.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-medium text-slate-600">{t('progress:questionTypesValid')}</div>
          <div className="flex flex-wrap gap-2">
            {qtypes.map(([qt, n]) => (
              <span key={qt} className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-800">
                {shortQuestionTypeLabel(qt, t)}: {n}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function GenerationDetailsBody({
  genStats,
  questionsDone,
  questionsTotal,
  pipelineStats,
  t,
}: {
  genStats: QueueStats;
  questionsDone: number;
  questionsTotal: number;
  pipelineStats?: PipelineStats;
  t: ProgressT;
}) {
  const val = pipelineStats?.generation.validation;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <MetricCard label={t('progress:mTotal')} value={questionsTotal} />
        <MetricCard label={t('progress:pending')} value={genStats.pendingCount} />
        <MetricCard label={t('progress:processing')} value={genStats.processingCount} />
        <MetricCard label={t('progress:completed')} value={genStats.completedCount} tone="success" />
        <MetricCard label={t('progress:failed')} value={genStats.failedCount} tone={genStats.failedCount ? 'danger' : 'default'} />
        <MetricCard label={t('progress:cancelled')} value={genStats.cancelledCount} />
      </div>
      <SegmentedProgressBar segments={queueSegments(t, genStats)} />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label={t('progress:questionsProgress')}
          value={`${questionsDone} / ${questionsTotal}`}
        />
        <MetricCard label={t('progress:validationPassed')} value={val?.passed ?? '—'} tone="success" />
        <MetricCard label={t('progress:validationFailed')} value={val?.failed ?? '—'} tone={val?.failed ? 'danger' : 'default'} />
        <MetricCard
          label={t('progress:passRate')}
          value={pct(val?.passed ?? 0, (val?.passed ?? 0) + (val?.failed ?? 0))}
        />
      </div>
    </div>
  );
}

function ChunkPreviewList({
  chunks,
  loading,
  emptyLabel,
  loadingLabel,
  t,
  passedOnly = false,
}: {
  chunks: ChunkRecord[];
  loading: boolean;
  emptyLabel: string;
  loadingLabel: string;
  t: ProgressT;
  passedOnly?: boolean;
}) {
  if (loading && !chunks.length) {
    return <p className="text-sm text-slate-500">{loadingLabel}</p>;
  }
  if (!chunks.length) {
    return <p className="text-sm text-slate-500">{emptyLabel}</p>;
  }

  const detailed = storedChunksToDetailed(chunks);
  return (
    <div className="max-h-96 space-y-2 overflow-y-auto">
      {detailed.slice(0, CHUNK_PREVIEW_LIMIT).map((ch, i) => {
        const idx = ch._chunk_index ?? i + 1;
        const text = chunkPreviewText(ch);
        const passed = ch._gate_passed;
        const gr = ch._gate_result;
        const blockType = chunkBlockType(ch);
        const pages = chunkPageRange(ch);
        const badge =
          passed === true
            ? t('progress:chunkGatePassed')
            : passed === false
              ? t('progress:chunkGateRejected')
              : t('progress:chunkGatePending');
        const badgeClass =
          passed === true
            ? 'bg-emerald-100 text-emerald-800'
            : passed === false
              ? 'bg-red-100 text-red-800'
              : 'bg-slate-100 text-slate-600';
        return (
          <div key={idx} className="rounded-lg border border-slate-100 bg-white p-2 text-xs">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span className="font-medium text-slate-800">#{idx}</span>
              {!passedOnly && (
                <span className={`rounded-full px-2 py-0.5 ${badgeClass}`}>{badge}</span>
              )}
              {gr?.rejection_reason && (
                <span className="text-red-700">{gateReasonLabel(gr.rejection_reason, t)}</span>
              )}
              {blockType && (
                <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-indigo-700">
                  {t(BLOCK_TYPE_LABEL_KEYS[blockType])}
                </span>
              )}
              {pages && (
                <span className="text-slate-400">
                  {pages[0] === pages[1]
                    ? t('progress:pageOne', { page: pages[0] })
                    : t('progress:pageRange', { from: pages[0], to: pages[1] })}
                </span>
              )}
              {(ch._question_types_valid?.length ?? 0) > 0 && (
                <span className="text-slate-500">
                  {ch._question_types_valid!.map((qt) => shortQuestionTypeLabel(qt, t)).join(', ')}
                </span>
              )}
            </div>
            <pre className="max-h-24 overflow-auto whitespace-pre-wrap text-slate-700">
              {text.slice(0, 500)}{text.length > 500 ? '…' : ''}
            </pre>
            {gr && (gr.c1_reasoning || gr.c2_reasoning || gr.c3_reasoning) && (
              <details className="mt-1">
                <summary className="cursor-pointer text-slate-500">{t('progress:gateReasoning')}</summary>
                <ul className="mt-1 space-y-0.5 text-slate-600">
                  {gr.c1_reasoning && <li>c1: {gr.c1_reasoning}</li>}
                  {gr.c2_reasoning && <li>c2: {gr.c2_reasoning}</li>}
                  {gr.c3_reasoning && <li>c3: {gr.c3_reasoning}</li>}
                </ul>
              </details>
            )}
          </div>
        );
      })}
      {chunks.length > CHUNK_PREVIEW_LIMIT && (
        <p className="text-xs text-slate-500">
          {t('progress:showingOf', { n: CHUNK_PREVIEW_LIMIT, total: chunks.length })}
        </p>
      )}
    </div>
  );
}

function TaskDrillDown({
  task,
  onCancel,
  cancelling,
}: {
  task: Task;
  onCancel?: () => void;
  cancelling?: boolean;
}) {
  const { t } = useTranslation(['progress']);
  return (
    <div className="card space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">{t('progress:taskTitle', { id: task._id })}</h3>
        {onCancel && (
          <button
            type="button"
            className="btn-secondary py-1 text-xs text-red-700"
            disabled={cancelling}
            onClick={(e) => {
              e.stopPropagation();
              onCancel();
            }}
          >
            {cancelling ? t('progress:cancelling') : t('progress:cancelTask')}
          </button>
        )}
      </div>
      <div className="grid gap-2 text-sm sm:grid-cols-2">
        <div><span className="text-slate-500">{t('progress:taskStatus')}:</span> {statusFilterLabel(t, task.status ?? '')}</div>
        <div><span className="text-slate-500">{t('progress:taskAttempts')}:</span> {task.attempts ?? 0}</div>
        <div><span className="text-slate-500">{t('progress:taskChunk')}:</span> {task.chunk_id}</div>
        <div><span className="text-slate-500">{t('progress:taskType')}:</span> {task.question_type ? shortQuestionTypeLabel(task.question_type, t) : '—'}</div>
        <div><span className="text-slate-500">{t('progress:taskDataset')}:</span> {task.dataset_name ?? '—'}</div>
        <div><span className="text-slate-500">{t('progress:taskSourceDoc')}:</span> {task.source_document ?? '—'}</div>
        <div><span className="text-slate-500">{t('progress:taskCreated')}:</span> {formatTimestamp(task.created_at)}</div>
      </div>
      {(taskMessageLabel(task, t) || task.error) && (
        <div>
          <div className="text-sm font-medium text-red-700">{t('progress:colError')}</div>
          {taskMessageLabel(task, t) && (
            <p className="mt-1 text-sm text-red-800">{taskMessageLabel(task, t)}</p>
          )}
          {task.error && (
            <details className="mt-1">
              <summary className="cursor-pointer text-xs text-slate-500">{t('progress:taskError')}</summary>
              <pre className="mt-1 overflow-x-auto rounded bg-red-50 p-2 text-xs text-red-800">{task.error}</pre>
            </details>
          )}
        </div>
      )}
      {task.chunk_text && (
        <details>
          <summary className="cursor-pointer text-sm font-medium">{t('progress:taskChunkPreview')}</summary>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs">{task.chunk_text}</pre>
        </details>
      )}
      {task.result != null && (
        <details>
          <summary className="cursor-pointer text-sm font-medium">{t('progress:taskResultJson')}</summary>
          <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-50 p-2 text-xs">{JSON.stringify(task.result, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
