import { useMemo, useState } from 'react';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listQueues,
  listDatasets,
  getDataset,
  listDatasetTasks,
  listQueueTasks,
  deleteQueue,
  retryFailedTasks,
  retryAllFailedTasks,
} from '@/shared/api/dataset';
import { listModelsHealth } from '@/shared/api/agent';
import {
  recountQueueAggregates,
  computeDatasetProgress,
  hasActiveWork,
} from '@/shared/lib/progress';
import { truncIso } from '@/shared/lib/labels';
import { usePagedList } from '@/shared/lib/usePagedList';
import { useSort } from '@/shared/lib/useSort';
import { useResizableColumns } from '@/shared/lib/useResizableColumns';
import { PageHeader } from '@/shared/ui/PageHeader';
import { MetricCard } from '@/shared/ui/MetricCard';
import { Pagination } from '@/shared/ui/Pagination';
import { SortableTh } from '@/shared/ui/SortableTh';
import { BarChartRc, PieChartRc, SegmentedProgressBar, type CategoryDatum, type ProgressSegment } from '@/shared/ui/charts';
import { StatusBanner, LoadingState, EmptyState } from '@/shared/ui/StatusBanner';
import type { QueueStats, Task } from '@/shared/types/documents';

function queueSegments(stats: QueueStats): ProgressSegment[] {
  return [
    { label: 'Completed', value: stats.completedCount, colorClass: 'bg-emerald-600' },
    { label: 'Processing', value: stats.processingCount, colorClass: 'bg-teal-500' },
    { label: 'Pending', value: stats.pendingCount, colorClass: 'bg-stone-300' },
    { label: 'Failed', value: stats.failedCount, colorClass: 'bg-red-500' },
    { label: 'Cancelled', value: stats.cancelledCount, colorClass: 'bg-amber-400' },
  ];
}

const POLL_MS = 5_000;
const STATUS_FILTERS = ['All', 'pending', 'processing', 'completed', 'failed', 'cancelled'];

export function QueueManagerPage() {
  const queryClient = useQueryClient();
  const [selectedQueue, setSelectedQueue] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState('All');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [queueSearch, setQueueSearch] = useState('');
  const [dsSearch, setDsSearch] = useState('');

  const { data: health = [] } = useQuery({
    queryKey: ['models-health'],
    queryFn: listModelsHealth,
    refetchInterval: autoRefresh ? POLL_MS : false,
  });

  const { data: queues = [], refetch: refetchQueues, isLoading: loadingQueues, isError: queuesError } = useQuery({
    queryKey: ['queues'],
    queryFn: listQueues,
    refetchInterval: autoRefresh ? POLL_MS : false,
  });

  const { data: datasets = [] } = useQuery({
    queryKey: ['datasets'],
    queryFn: listDatasets,
    refetchInterval: autoRefresh ? POLL_MS : false,
  });

  const isActive = autoRefresh && hasActiveWork(queues);

  const healthMap = useMemo(
    () => Object.fromEntries(health.map((h) => [h.id, h])),
    [health],
  );
  const unavailable = health.filter((h) => !h.available);

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
      queryFn: () => listQueueTasks(q.name),
      refetchInterval: autoRefresh && isActive ? POLL_MS : false,
    })),
  });

  const recountMap = useMemo(() => {
    const map: Record<string, QueueStats> = {};
    queuesNeedingRecount.forEach((q, i) => {
      const tasks = recountResults[i]?.data;
      if (tasks) map[q.name] = recountQueueAggregates(tasks);
    });
    return map;
  }, [queuesNeedingRecount, recountResults]);

  const queueStats = useMemo(
    () =>
      queues.map((q) => {
        const fallback: QueueStats = {
          taskCount: q.task_count ?? 0,
          pendingCount: q.pending_count ?? 0,
          processingCount: q.processing_count ?? 0,
          completedCount: q.completed_count ?? 0,
          failedCount: q.failed_count ?? 0,
          cancelledCount: q.cancelled_count ?? 0,
        };
        return { queue: q, stats: recountMap[q.name] ?? fallback };
      }),
    [queues, recountMap],
  );

  const activeQueue = selectedQueue ?? queues[0]?.name ?? null;

  const { data: queueTasks = [] } = useQuery({
    queryKey: ['queue-tasks', activeQueue],
    queryFn: () => listQueueTasks(activeQueue!),
    enabled: !!activeQueue,
    refetchInterval: autoRefresh && isActive ? POLL_MS : false,
  });

  const selectedStats = useMemo(() => recountQueueAggregates(queueTasks), [queueTasks]);

  // Warn if any model used by tasks in the selected queue is down.
  const downModels = useMemo(() => {
    const ids = new Set<string>();
    for (const t of queueTasks) {
      for (const id of [t.generation_model_id, t.validation_model_id]) {
        if (id && healthMap[id] && !healthMap[id].available) ids.add(id);
      }
    }
    return [...ids].map((id) => healthMap[id]?.name ?? id);
  }, [queueTasks, healthMap]);

  const filteredTasks =
    statusFilter === 'All' ? queueTasks : queueTasks.filter((t) => t.status === statusFilter);
  const failedTasks = queueTasks.filter((t) => t.status === 'failed');

  const statusChart: CategoryDatum[] = useMemo(() => {
    const total = queueStats.reduce((acc, { stats }) => {
      acc.pending += stats.pendingCount;
      acc.processing += stats.processingCount;
      acc.completed += stats.completedCount;
      acc.failed += stats.failedCount;
      acc.cancelled += stats.cancelledCount;
      return acc;
    }, { pending: 0, processing: 0, completed: 0, failed: 0, cancelled: 0 });
    return Object.entries(total)
      .filter(([, v]) => v > 0)
      .map(([label, value]) => ({ label, value }));
  }, [queueStats]);

  const queueChart: CategoryDatum[] = useMemo(
    () => queueStats.filter(({ stats }) => stats.taskCount > 0).map(({ queue, stats }) => ({ label: queue.name, value: stats.taskCount })),
    [queueStats],
  );

  const { data: datasetProgressRows = [] } = useQuery({
    queryKey: ['dataset-progress', datasets.map((d) => d._id).join(',')],
    queryFn: async () => {
      const rows = [];
      for (const ds of datasets) {
        const dsFull = await getDataset(ds._id);
        const tasks = await listDatasetTasks(ds._id);
        const prog = computeDatasetProgress(ds, dsFull, tasks);
        const meta = dsFull?.metadata ?? ds.metadata ?? {};
        rows.push({
          name: ds.name,
          status: prog.status,
          chunks: `${ds.chunks_valid ?? 0}/${meta.total_chunks ?? ds.chunks_count ?? 0}`,
          questionsGenerated: prog.totalQuestionsGenerated,
          expected: prog.expectedQuestions,
          progress: prog.progressPercent,
          created: truncIso(ds.created_at),
          lastUpdated: truncIso(ds.updated_at ?? meta.last_updated),
        });
      }
      return rows;
    },
    refetchInterval: autoRefresh && isActive ? POLL_MS : false,
  });

  async function handleRetry() {
    if (!activeQueue) return;
    await retryFailedTasks(activeQueue);
    refetchQueues();
  }

  const [retryingAll, setRetryingAll] = useState(false);
  const [retryMsg, setRetryMsg] = useState<string | null>(null);
  const totalFailed = useMemo(
    () => queueStats.reduce((acc, { stats }) => acc + stats.failedCount, 0),
    [queueStats],
  );

  async function handleRetryAll() {
    if (!totalFailed) return;
    if (!window.confirm(
      `Restart all ${totalFailed} failed tasks across every queue? ` +
      `They will be reset to "pending" and reprocessed by the worker.`,
    )) return;
    setRetryingAll(true);
    setRetryMsg(null);
    try {
      const res = await retryAllFailedTasks();
      setRetryMsg(`Re-queued ${res.tasks_retried} failed tasks (${res.datasets_reopened} datasets reopened).`);
      refetchQueues();
      queryClient.invalidateQueries({ queryKey: ['dataset-progress'] });
    } catch {
      setRetryMsg('Failed to restart tasks. Check API connection and permissions.');
    } finally {
      setRetryingAll(false);
    }
  }

  async function handleDelete() {
    if (!activeQueue || !window.confirm(`Delete queue ${activeQueue}?`)) return;
    await deleteQueue(activeQueue);
    setSelectedQueue(null);
    refetchQueues();
  }

  function modelCell(id?: string) {
    if (!id) return '—';
    const h = healthMap[id];
    if (!h) return id;
    return h.available ? h.name : `⚠ ${h.name}`;
  }

  type QueueRow = (typeof queueStats)[number];
  const queueSort = useSort<QueueRow>();
  const queueCols = useResizableColumns({ name: 260, description: 240 });
  const queuesSorted = queueSort.sortItems(queueStats, {
    name: ({ queue }) => queue.name,
    description: ({ queue }) => queue.description,
    priority: ({ queue }) => queue.priority ?? 0,
    total: ({ stats }) => stats.taskCount,
    pending: ({ stats }) => stats.pendingCount,
    processing: ({ stats }) => stats.processingCount,
    completed: ({ stats }) => stats.completedCount,
    failed: ({ stats }) => stats.failedCount,
    cancelled: ({ stats }) => stats.cancelledCount,
    created: ({ queue }) => queue.created_at,
  });
  const queuesPaged = usePagedList(queuesSorted, {
    pageSize: 8,
    search: queueSearch,
    searchFields: ({ queue }) => [queue.name, queue.description],
  });

  type DsRow = (typeof datasetProgressRows)[number];
  const dsSort = useSort<DsRow>();
  const dsCols = useResizableColumns({ name: 260 });
  const dsSorted = dsSort.sortItems(datasetProgressRows, {
    name: (r) => r.name,
    status: (r) => r.status,
    chunks: (r) => r.chunks,
    questions: (r) => r.questionsGenerated,
    progress: (r) => r.progress,
    created: (r) => r.created,
    lastUpdated: (r) => r.lastUpdated,
  });
  const dsPaged = usePagedList(dsSorted, {
    pageSize: 8,
    search: dsSearch,
    searchFields: (r) => [r.name, r.status],
  });

  const taskSort = useSort<Task>();
  const taskCols = useResizableColumns({ id: 220, gen: 170, val: 170, error: 280 });
  const tasksSorted = taskSort.sortItems(filteredTasks, {
    id: (t) => t._id,
    chunk: (t) => t.chunk_id,
    type: (t) => t.question_type,
    status: (t) => t.status,
    priority: (t) => t.priority ?? 0,
    gen: (t) => t.generation_model_id,
    val: (t) => t.validation_model_id,
    updated: (t) => t.updated_at,
    error: (t) => t.error ?? '',
  });
  const tasksPaged = usePagedList(tasksSorted, { pageSize: 25 });

  if (loadingQueues) return <LoadingState label="Loading queues…" />;

  return (
    <div>
      <PageHeader
        title="Queue Manager"
        subtitle="Monitor task queues, dataset progress, and model health."
        actions={
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
            Auto-refresh (5s)
          </label>
        }
      />

      {queuesError && (
        <StatusBanner tone="error">Could not load queues. Check API connection and authentication.</StatusBanner>
      )}

      <section className="mt-6">
        <h2 className="font-semibold">Model Health</h2>
        <div className="mt-2">
          {unavailable.length ? (
            <StatusBanner tone="error">
              Models not responding: {unavailable.map((h) => h.name).join(', ')}
            </StatusBanner>
          ) : health.length ? (
            <StatusBanner tone="success">All {health.length} models are healthy.</StatusBanner>
          ) : (
            <p className="text-slate-500">No model health data.</p>
          )}
        </div>
      </section>

      <section className="mt-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold">Queue Overview</h2>
          {queues.length > 0 && (
            <button
              type="button"
              className="btn-secondary py-1.5 text-sm disabled:opacity-50"
              onClick={() => void handleRetryAll()}
              disabled={retryingAll || totalFailed === 0}
              title="Reset every failed task to pending and reprocess"
            >
              {retryingAll ? 'Restarting…' : `Restart failed tasks${totalFailed ? ` (${totalFailed})` : ''}`}
            </button>
          )}
        </div>
        {retryMsg && (
          <div className="mt-2">
            <StatusBanner tone="info">{retryMsg}</StatusBanner>
          </div>
        )}
        {!queues.length ? (
          <EmptyState title="No queues yet" description="Create a queue by running generation in Data Preprocessing." />
        ) : (
          <>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
              <input
                type="search"
                className="input-field max-w-xs"
                placeholder="Search queues…"
                value={queueSearch}
                onChange={(e) => { setQueueSearch(e.target.value); queuesPaged.setPage(1); }}
              />
              <Pagination
                page={queuesPaged.page}
                totalPages={queuesPaged.totalPages}
                total={queuesPaged.total}
                onPageChange={queuesPaged.setPage}
                label="queues"
              />
            </div>

            <div className="mt-2 w-full overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="data-table" ref={queueCols.tableRef} style={queueCols.tableStyle}>
                <thead>
                  <tr>
                    <SortableTh label="Queue" sortKey="name" sort={queueSort.sort} onSort={queueSort.requestSort} resize={queueCols} />
                    <SortableTh label="Description" sortKey="description" sort={queueSort.sort} onSort={queueSort.requestSort} resize={queueCols} />
                    <SortableTh label="Priority" sortKey="priority" sort={queueSort.sort} onSort={queueSort.requestSort} resize={queueCols} />
                    <SortableTh label="Total" sortKey="total" sort={queueSort.sort} onSort={queueSort.requestSort} resize={queueCols} />
                    <SortableTh label="Pending" sortKey="pending" sort={queueSort.sort} onSort={queueSort.requestSort} resize={queueCols} />
                    <SortableTh label="Processing" sortKey="processing" sort={queueSort.sort} onSort={queueSort.requestSort} resize={queueCols} />
                    <SortableTh label="Completed" sortKey="completed" sort={queueSort.sort} onSort={queueSort.requestSort} resize={queueCols} />
                    <SortableTh label="Failed" sortKey="failed" sort={queueSort.sort} onSort={queueSort.requestSort} resize={queueCols} />
                    <SortableTh label="Cancelled" sortKey="cancelled" sort={queueSort.sort} onSort={queueSort.requestSort} resize={queueCols} />
                    <SortableTh label="Created" sortKey="created" sort={queueSort.sort} onSort={queueSort.requestSort} resize={queueCols} />
                  </tr>
                </thead>
                <tbody>
                  {queuesPaged.pageItems.map(({ queue, stats }) => (
                    <tr key={queue.name}>
                      <td className="whitespace-normal break-words align-top font-medium" title={queue.name}>{queue.name}</td>
                      <td className="whitespace-normal break-words align-top text-slate-500" title={queue.description}>{queue.description ?? '—'}</td>
                      <td>{queue.priority ?? 0}</td>
                      <td>{stats.taskCount}</td>
                      <td>{stats.pendingCount}</td>
                      <td>{stats.processingCount}</td>
                      <td>{stats.completedCount}</td>
                      <td>{stats.failedCount}</td>
                      <td>{stats.cancelledCount}</td>
                      <td className="whitespace-nowrap text-xs text-slate-500">{truncIso(queue.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="card mt-6">
              <h3 className="mb-4 font-semibold">Queue Progress</h3>
              <div className="space-y-4">
                {queuesPaged.pageItems.map(({ queue, stats }) => (
                  <SegmentedProgressBar key={queue.name} title={queue.name} segments={queueSegments(stats)} />
                ))}
              </div>
            </div>

            <div className="mt-6 grid gap-6 lg:grid-cols-2">
              <PieChartRc title="Tasks by Status" data={statusChart} />
              <BarChartRc title="Tasks by Queue" data={queueChart} color="#3f6b4f" />
            </div>
          </>
        )}
      </section>

      {datasetProgressRows.length > 0 && (
        <section className="mt-8">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
            <h2 className="font-semibold">Dataset Progress</h2>
            <div className="flex flex-wrap items-center gap-3">
              <input
                type="search"
                className="input-field max-w-xs"
                placeholder="Search datasets…"
                value={dsSearch}
                onChange={(e) => { setDsSearch(e.target.value); dsPaged.setPage(1); }}
              />
              <Pagination
                page={dsPaged.page}
                totalPages={dsPaged.totalPages}
                total={dsPaged.total}
                onPageChange={dsPaged.setPage}
                label="datasets"
              />
            </div>
          </div>
          <div className="w-full overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="data-table" ref={dsCols.tableRef} style={dsCols.tableStyle}>
              <thead>
                <tr>
                  <SortableTh label="Dataset" sortKey="name" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                  <SortableTh label="Status" sortKey="status" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                  <SortableTh label="Chunks" sortKey="chunks" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                  <SortableTh label="Questions" sortKey="questions" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                  <SortableTh label="Progress" sortKey="progress" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} className="min-w-[10rem]" />
                  <SortableTh label="Created" sortKey="created" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                  <SortableTh label="Last Updated" sortKey="lastUpdated" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                </tr>
              </thead>
              <tbody>
                {dsPaged.pageItems.map((row) => (
                  <tr key={row.name}>
                    <td className="whitespace-normal break-words align-top font-medium" title={row.name}>{row.name}</td>
                    <td>{row.status}</td>
                    <td className="whitespace-nowrap">{row.chunks}</td>
                    <td className="whitespace-nowrap">{row.questionsGenerated}/{row.expected}</td>
                    <td>
                      <div className="flex items-center gap-2">
                        <div className="h-2 w-24 overflow-hidden rounded-full bg-slate-100">
                          <div className="h-full bg-gena-primary" style={{ width: `${Math.min(100, row.progress)}%` }} />
                        </div>
                        <span className="text-xs text-slate-500">{row.progress.toFixed(0)}%</span>
                      </div>
                    </td>
                    <td className="whitespace-nowrap text-xs text-slate-500">{row.created}</td>
                    <td className="whitespace-nowrap text-xs text-slate-500">{row.lastUpdated}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {queues.length > 0 && (
        <section className="mt-8">
          <h2 className="font-semibold">Manage Queues</h2>
          <select
            className="input-field mt-2 max-w-xl"
            value={activeQueue ?? ''}
            onChange={(e) => { setSelectedQueue(e.target.value); setOpenTaskId(null); }}
          >
            {queues.map((q) => (
              <option key={q.name} value={q.name}>
                {q.name} ({q.task_count ?? 0} tasks)
              </option>
            ))}
          </select>

          {activeQueue && (
            <div className="mt-4 space-y-4">
              {downModels.length > 0 && (
                <StatusBanner tone="warning">
                  Models used by this queue are down: {downModels.join(', ')}
                </StatusBanner>
              )}

              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
                <MetricCard label="Total" value={selectedStats.taskCount} />
                <MetricCard label="Pending" value={selectedStats.pendingCount} />
                <MetricCard label="Processing" value={selectedStats.processingCount} />
                <MetricCard label="Completed" value={selectedStats.completedCount} tone="success" />
                <MetricCard label="Failed" value={selectedStats.failedCount} tone={selectedStats.failedCount ? 'danger' : 'default'} />
                <MetricCard label="Cancelled" value={selectedStats.cancelledCount} />
              </div>

              <div className="card">
                <SegmentedProgressBar segments={queueSegments(selectedStats)} />
              </div>

              {failedTasks.length > 0 && (
                <details className="rounded-xl border border-red-200 bg-red-50/50 p-4">
                  <summary className="cursor-pointer font-medium text-red-800">
                    {failedTasks.length} failed task{failedTasks.length > 1 ? 's' : ''}
                  </summary>
                  <ul className="mt-3 space-y-2 text-xs">
                    {failedTasks.map((t) => (
                      <li key={t._id} className="rounded border border-red-100 bg-white p-2">
                        <span className="font-mono">{t._id}</span> · chunk {t.chunk_id} · {t.question_type}
                        {t.error && <div className="mt-1 text-red-700">{t.error}</div>}
                      </li>
                    ))}
                  </ul>
                  <button type="button" className="btn-primary mt-3" onClick={() => void handleRetry()}>
                    Retry Failed Tasks
                  </button>
                </details>
              )}

              <div className="flex flex-wrap items-center justify-between gap-3">
                <select className="input-field max-w-xs" value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); tasksPaged.setPage(1); }}>
                  {STATUS_FILTERS.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
                <Pagination
                  page={tasksPaged.page}
                  totalPages={tasksPaged.totalPages}
                  total={tasksPaged.total}
                  onPageChange={tasksPaged.setPage}
                  showInput
                  label="tasks"
                />
              </div>

              <div className="w-full overflow-x-auto rounded-xl border border-slate-200 bg-white">
                <table className="data-table text-xs" ref={taskCols.tableRef} style={taskCols.tableStyle}>
                  <thead>
                    <tr>
                      <SortableTh label="ID" sortKey="id" sort={taskSort.sort} onSort={taskSort.requestSort} resize={taskCols} />
                      <SortableTh label="Chunk" sortKey="chunk" sort={taskSort.sort} onSort={taskSort.requestSort} resize={taskCols} />
                      <SortableTh label="Type" sortKey="type" sort={taskSort.sort} onSort={taskSort.requestSort} resize={taskCols} />
                      <SortableTh label="Status" sortKey="status" sort={taskSort.sort} onSort={taskSort.requestSort} resize={taskCols} />
                      <SortableTh label="Priority" sortKey="priority" sort={taskSort.sort} onSort={taskSort.requestSort} resize={taskCols} />
                      <SortableTh label="Gen Model" sortKey="gen" sort={taskSort.sort} onSort={taskSort.requestSort} resize={taskCols} />
                      <SortableTh label="Val Model" sortKey="val" sort={taskSort.sort} onSort={taskSort.requestSort} resize={taskCols} />
                      <SortableTh label="Updated" sortKey="updated" sort={taskSort.sort} onSort={taskSort.requestSort} resize={taskCols} />
                      <SortableTh label="Error" sortKey="error" sort={taskSort.sort} onSort={taskSort.requestSort} resize={taskCols} />
                    </tr>
                  </thead>
                  <tbody>
                    {tasksPaged.pageItems.map((t) => (
                      <tr
                        key={t._id}
                        className="cursor-pointer hover:bg-slate-50"
                        onClick={() => setOpenTaskId(openTaskId === t._id ? null : t._id)}
                      >
                        <td className="whitespace-normal break-all align-top font-mono" title={t._id}>{t._id}</td>
                        <td>{t.chunk_id}</td>
                        <td>{t.question_type}</td>
                        <td>{t.status}</td>
                        <td>{t.priority ?? 0}</td>
                        <td className="whitespace-normal break-words align-top" title={t.generation_model_id}>{modelCell(t.generation_model_id)}</td>
                        <td className="whitespace-normal break-words align-top" title={t.validation_model_id}>{modelCell(t.validation_model_id)}</td>
                        <td className="whitespace-nowrap align-top text-slate-500">{truncIso(t.updated_at)}</td>
                        <td className="whitespace-normal break-words align-top text-red-700" title={t.error}>{t.error}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {openTaskId && (() => {
                const t = queueTasks.find((x) => x._id === openTaskId);
                if (!t) return null;
                return <TaskDrillDown task={t} />;
              })()}

              <button type="button" className="btn-secondary text-red-700" onClick={() => void handleDelete()}>
                Delete Queue
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function TaskDrillDown({ task }: { task: Task }) {
  return (
    <div className="card space-y-3">
      <h3 className="font-semibold">Task {task._id}</h3>
      <div className="grid gap-2 text-sm sm:grid-cols-2">
        <div><span className="text-slate-500">Status:</span> {task.status}</div>
        <div><span className="text-slate-500">Chunk:</span> {task.chunk_id}</div>
        <div><span className="text-slate-500">Type:</span> {task.question_type}</div>
        <div><span className="text-slate-500">Dataset:</span> {task.dataset_name ?? '—'}</div>
        <div><span className="text-slate-500">Source doc:</span> {task.source_document ?? '—'}</div>
        <div><span className="text-slate-500">Created:</span> {truncIso(task.created_at)}</div>
      </div>
      {task.error && (
        <div>
          <div className="text-sm font-medium text-red-700">Error</div>
          <pre className="mt-1 overflow-x-auto rounded bg-red-50 p-2 text-xs text-red-800">{task.error}</pre>
        </div>
      )}
      {task.chunk_text && (
        <details>
          <summary className="cursor-pointer text-sm font-medium">Chunk preview</summary>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs">{task.chunk_text}</pre>
        </details>
      )}
      {task.result != null && (
        <details>
          <summary className="cursor-pointer text-sm font-medium">Result JSON</summary>
          <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-50 p-2 text-xs">{JSON.stringify(task.result, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
