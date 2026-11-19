import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listQueues,
  listDatasets,
  listQueueTasks,
  retryAllFailedTasks,
  deleteQueue,
} from '@/shared/api/dataset';
import { listModelsHealth } from '@/shared/api/agent';
import { ModelHealthBanner, availableHealth } from './ModelHealthBanner';
import {
  generationProgressPercent,
  generationStatus,
  queueStatsFromQueue,
  recountQueueAggregates,
  type GenerationStatus,
} from '@/shared/lib/progress';
import { isExpert, useAuth } from '@/features/auth/authStore';
import { AUTH_ENABLED } from '@/shared/config/auth';
import { usePageTitle } from '@/shared/i18n/usePageTitle';
import { formatTimestamp, timestampMs } from '@/shared/lib/dateTime';
import { humanQueueName } from '@/shared/lib/labels';
import { usePagedList } from '@/shared/lib/usePagedList';
import { useSort } from '@/shared/lib/useSort';
import { useResizableColumns } from '@/shared/lib/useResizableColumns';
import { PageHeader } from '@/shared/ui/PageHeader';
import { Pagination } from '@/shared/ui/Pagination';
import { SortableTh } from '@/shared/ui/SortableTh';
import { CollapsibleSection } from '@/shared/ui/CollapsibleSection';
import { StatusBanner, LoadingState, EmptyState } from '@/shared/ui/StatusBanner';
import type { Dataset, Queue, QueueStats } from '@/shared/types/documents';

const POLL_MS = 5_000;
const TASK_FETCH_LIMIT = 5000;

interface GenerationRow {
  queue: Queue;
  stats: QueueStats;
  dataset: Dataset | null;
  status: GenerationStatus;
  chunksValid: number | null;
  chunksTotal: number | null;
  questionsDone: number | null;
  questionsExpected: number | null;
  /** Percent 0–100, or null when there is no linked dataset / expectation. */
  progress: number | null;
}

export function QueueManagerPage() {
  const { t } = useTranslation(['progress', 'common', 'nav']);
  usePageTitle('progress', 'nav');
  const auth = useAuth();
  const canRunExpertActions = !AUTH_ENABLED || isExpert(auth.role);

  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [queueSearch, setQueueSearch] = useState('');
  const [retryingAll, setRetryingAll] = useState(false);
  const [retryMsg, setRetryMsg] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deletingName, setDeletingName] = useState<string | null>(null);

  const { data: health = [] } = useQuery({
    queryKey: ['models-health'],
    queryFn: listModelsHealth,
    refetchInterval: autoRefresh ? POLL_MS : false,
  });

  const { data: queues = [], isLoading: loadingQueues, isError: queuesError } = useQuery({
    queryKey: ['queues'],
    queryFn: listQueues,
    refetchInterval: autoRefresh ? POLL_MS : false,
  });

  const { data: datasets = [] } = useQuery({
    queryKey: ['datasets'],
    queryFn: () => listDatasets(),
    refetchInterval: autoRefresh ? POLL_MS : false,
  });

  // Недоступные модели не показываем; тревога — только когда не отвечает ни одна.
  const hasModelIssues = health.length > 0 && availableHealth(health).length === 0;

  // Recount aggregates for every queue that is missing counts by fetching its tasks.
  const queuesNeedingRecount = useMemo(
    () =>
      queues.filter((q) => {
        const sum =
          (q.pending_count ?? 0) +
          (q.processing_count ?? 0) +
          (q.completed_count ?? 0) +
          (q.failed_count ?? 0) +
          (q.cancelled_count ?? 0);
        return !q.task_count || sum === 0;
      }),
    [queues],
  );

  const recountResults = useQueries({
    queries: queuesNeedingRecount.map((q) => ({
      queryKey: ['queue-recount', q.name],
      queryFn: () => listQueueTasks(q.name, undefined, TASK_FETCH_LIMIT, true),
      refetchInterval: autoRefresh && queuesNeedingRecount.length > 0 ? POLL_MS : false,
    })),
  });

  const recountMap = useMemo(() => {
    const map: Record<string, QueueStats> = {};
    queuesNeedingRecount.forEach((q, i) => {
      const tasks = recountResults[i]?.data;
      if (!tasks) return;
      const taskCount = q.task_count ?? 0;
      const truncated =
        taskCount > 0 &&
        tasks.length >= TASK_FETCH_LIMIT &&
        tasks.length < taskCount;
      if (!truncated) map[q.name] = recountQueueAggregates(tasks);
    });
    return map;
  }, [queuesNeedingRecount, recountResults]);

  const datasetByQueue = useMemo(() => {
    const map: Record<string, Dataset> = {};
    for (const d of datasets) {
      const qn = d.metadata?.queue_name;
      if (qn) map[qn] = d;
    }
    return map;
  }, [datasets]);

  const generationRows = useMemo<GenerationRow[]>(
    () =>
      queues.map((q) => {
        const stats = recountMap[q.name] ?? queueStatsFromQueue(q);
        const dataset = datasetByQueue[q.name] ?? null;
        const meta = dataset?.metadata;
        const questionsDone = dataset
          ? Number(meta?.total_questions_generated ?? dataset.questions_count ?? 0)
          : null;
        const questionsExpected = dataset ? Number(meta?.expected_questions ?? 0) : null;
        const progress = generationProgressPercent(questionsDone, questionsExpected, stats);
        return {
          queue: q,
          stats,
          dataset,
          status: generationStatus(stats),
          chunksValid: dataset ? dataset.chunks_valid ?? 0 : null,
          chunksTotal: dataset ? dataset.chunks_count ?? 0 : null,
          questionsDone,
          questionsExpected,
          progress,
        };
      }),
    [queues, recountMap, datasetByQueue],
  );

  const totalFailed = useMemo(
    () => generationRows.reduce((acc, { stats }) => acc + stats.failedCount, 0),
    [generationRows],
  );

  async function handleRetryAll() {
    if (!totalFailed) return;
    if (!window.confirm(
      t('progress:restartFailedAllConfirm', { n: totalFailed }),
    )) return;
    setRetryingAll(true);
    setRetryMsg(null);
    setActionError(null);
    try {
      const res = await retryAllFailedTasks();
      setRetryMsg(
        t('progress:retriedAllFailed', {
          n: res.tasks_retried,
          datasets: res.datasets_reopened
            ? t('progress:datasetsReopened', { n: res.datasets_reopened })
            : '',
        }),
      );
      await queryClient.invalidateQueries({ queryKey: ['queues'] });
      await queryClient.invalidateQueries({ queryKey: ['queue-tasks'] });
      await queryClient.invalidateQueries({ queryKey: ['queue-recount'] });
    } catch (e) {
      setActionError(e instanceof Error ? e.message : t('progress:retryAllFailedGeneric'));
    } finally {
      setRetryingAll(false);
    }
  }

  async function handleDeleteQueue(queue: Queue) {
    if (deletingName) return;
    const display = humanQueueName(queue.name);
    if (!window.confirm(t('progress:deleteQueueConfirm', { name: display }))) return;
    setDeletingName(queue.name);
    setActionError(null);
    setRetryMsg(null);
    try {
      await deleteQueue(queue.name);
      await queryClient.invalidateQueries({ queryKey: ['queues'] });
      await queryClient.invalidateQueries({ queryKey: ['datasets'] });
    } catch (e) {
      setActionError(e instanceof Error ? e.message : t('progress:deleteFailed'));
    } finally {
      setDeletingName(null);
    }
  }

  function openQueue(name: string) {
    navigate(`/queue_overview?queue=${encodeURIComponent(name)}`);
  }

  const genSort = useSort<GenerationRow>({ key: 'created', dir: 'desc' });
  const genCols = useResizableColumns({ name: 320 });
  const rowsSorted = genSort.sortItems(generationRows, {
    name: ({ queue }) => humanQueueName(queue.name),
    status: ({ status }) => status,
    chunks: ({ chunksValid }) => chunksValid ?? -1,
    questions: ({ questionsDone }) => questionsDone ?? -1,
    progress: ({ progress }) => progress ?? -1,
    failed: ({ stats }) => stats.failedCount,
    created: ({ queue }) => timestampMs(queue.created_at),
  });
  const rowsPaged = usePagedList(rowsSorted, {
    pageSize: 8,
    search: queueSearch,
    searchFields: ({ queue, dataset }) => [
      queue.name,
      queue.description,
      humanQueueName(queue.name),
      dataset?.name,
    ],
  });

  if (loadingQueues) return <LoadingState label={t('progress:loadingQueues')} />;

  return (
    <div>
      <PageHeader
        title={t('progress:title')}
        subtitle={t('progress:subtitle')}
        actions={
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
            {t('progress:autoRefresh')}
          </label>
        }
      />

      {queuesError && (
        <StatusBanner tone="error">{t('progress:couldNotLoadQueues')}</StatusBanner>
      )}
      {retryMsg && (
        <div className="mt-4"><StatusBanner tone="info">{retryMsg}</StatusBanner></div>
      )}
      {actionError && (
        <div className="mt-4"><StatusBanner tone="error">{actionError}</StatusBanner></div>
      )}

      <section className="mt-6">
        <h2 className="font-semibold">{t('progress:queues')}</h2>
        {!queues.length ? (
          <EmptyState title={t('progress:noQueues')} />
        ) : (
          <>
            <p className="mt-1 text-sm text-slate-500">{t('progress:clickQueueHint')}</p>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
              <input
                type="search"
                className="input-field max-w-xs"
                placeholder={t('progress:searchQueues')}
                value={queueSearch}
                onChange={(e) => { setQueueSearch(e.target.value); rowsPaged.setPage(1); }}
              />
              <Pagination
                page={rowsPaged.page}
                totalPages={rowsPaged.totalPages}
                total={rowsPaged.total}
                onPageChange={rowsPaged.setPage}
                countLabel={(n) => t('common:queues', { count: n })}
              />
            </div>

            <div className="mt-2 w-full overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="data-table" ref={genCols.tableRef} style={genCols.tableStyle}>
                <thead>
                  <tr>
                    <SortableTh label={t('progress:colQueue')} sortKey="name" sort={genSort.sort} onSort={genSort.requestSort} resize={genCols} />
                    <SortableTh label={t('progress:colStatus')} sortKey="status" sort={genSort.sort} onSort={genSort.requestSort} resize={genCols} />
                    <SortableTh label={t('progress:colChunks')} titleHint={t('progress:tipChunks')} sortKey="chunks" sort={genSort.sort} onSort={genSort.requestSort} resize={genCols} />
                    <SortableTh label={t('progress:colQuestions')} titleHint={t('progress:tipQuestions')} sortKey="questions" sort={genSort.sort} onSort={genSort.requestSort} resize={genCols} />
                    <SortableTh label={t('progress:colProgress')} titleHint={t('progress:tipProgress')} sortKey="progress" sort={genSort.sort} onSort={genSort.requestSort} resize={genCols} className="min-w-[9rem]" />
                    <SortableTh label={t('progress:colErrors')} titleHint={t('progress:tipErrors')} sortKey="failed" sort={genSort.sort} onSort={genSort.requestSort} resize={genCols} />
                    <SortableTh label={t('progress:colCreated')} sortKey="created" sort={genSort.sort} onSort={genSort.requestSort} resize={genCols} />
                    {canRunExpertActions && <th>{t('progress:colActions')}</th>}
                  </tr>
                </thead>
                <tbody>
                  {rowsPaged.pageItems.map((row) => (
                    <tr
                      key={row.queue.name}
                      className="cursor-pointer hover:bg-slate-50"
                      onClick={() => openQueue(row.queue.name)}
                      title={t('progress:openQueue')}
                    >
                      <td className="whitespace-normal break-words align-top font-medium text-gena-primary" title={row.queue.name}>
                        {humanQueueName(row.queue.name)}
                      </td>
                      <td className="whitespace-nowrap">
                        {row.status === 'in_progress'
                          ? t('progress:genStatusRunning')
                          : row.status === 'cancelled'
                            ? t('progress:cancelled')
                            : row.status === 'completed_with_failures'
                              ? t('progress:completedWithFailures')
                              : t('progress:completed')}
                      </td>
                      <td className="whitespace-nowrap" title={t('progress:tipChunks')}>
                        {row.chunksValid != null ? `${row.chunksValid}/${row.chunksTotal}` : '—'}
                      </td>
                      <td className="whitespace-nowrap" title={t('progress:tipQuestions')}>
                        {row.questionsDone != null ? `${row.questionsDone}/${row.questionsExpected}` : '—'}
                      </td>
                      <td title={t('progress:tipProgress')}>
                        {row.progress != null ? (
                          <div className="flex items-center gap-2">
                            <div className="h-2 w-24 overflow-hidden rounded-full bg-slate-100">
                              <div className="h-full bg-gena-primary" style={{ width: `${row.progress}%` }} />
                            </div>
                            <span className="text-xs text-slate-500">{row.progress}%</span>
                          </div>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td
                        className={row.stats.failedCount > 0 ? 'font-semibold text-red-700' : undefined}
                        title={t('progress:tipErrors')}
                      >
                        {row.stats.failedCount}
                      </td>
                      <td className="whitespace-nowrap text-slate-500">{formatTimestamp(row.queue.created_at)}</td>
                      {canRunExpertActions && (
                        <td className="whitespace-nowrap">
                          <button
                            type="button"
                            className="btn-secondary py-1 text-xs text-red-700 disabled:opacity-50"
                            onClick={(e) => { e.stopPropagation(); void handleDeleteQueue(row.queue); }}
                            disabled={deletingName !== null}
                          >
                            {deletingName === row.queue.name ? t('progress:deleting') : t('progress:deleteAction')}
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <section className="mt-8">
        <CollapsibleSection
          title={`${hasModelIssues ? '⚠️ ' : ''}${t('progress:adminSection')}`}
        >
          <div className="space-y-4">
            <ModelHealthBanner health={health} />

            {canRunExpertActions && (
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-sm text-slate-600">{t('progress:retryFailedLabel')}</span>
                <button
                  type="button"
                  className="btn-secondary py-1.5 text-sm disabled:opacity-50"
                  onClick={() => void handleRetryAll()}
                  disabled={retryingAll || totalFailed === 0}
                  title={t('progress:restartFailedAllConfirm', { n: totalFailed })}
                >
                  {retryingAll ? t('progress:restarting') : t('progress:restartFailed', { n: totalFailed })}
                </button>
              </div>
            )}
          </div>
        </CollapsibleSection>
      </section>
    </div>
  );
}
