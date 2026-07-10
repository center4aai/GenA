import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listDatasets } from '@/shared/api/dataset';
import { isValidationPassed, parseValidationScore } from '@/shared/lib/progress';
import { downloadCsv } from '@/shared/lib/extendedExport';
import { questionTypeLabel, levelLabel, toCategoryData, truncIso, isoDate } from '@/shared/lib/labels';
import { computeStatistics, statisticsQueryKey, type DsStatRow } from './statisticsData';
import { usePagedList } from '@/shared/lib/usePagedList';
import { useSort } from '@/shared/lib/useSort';
import { useResizableColumns } from '@/shared/lib/useResizableColumns';
import { PageHeader } from '@/shared/ui/PageHeader';
import { MetricCard } from '@/shared/ui/MetricCard';
import { Pagination } from '@/shared/ui/Pagination';
import { SortableTh } from '@/shared/ui/SortableTh';
import { BarChartRc, PieChartRc, LineChartRc, StackedProgressBar } from '@/shared/ui/charts';
import { LoadingState, EmptyState } from '@/shared/ui/StatusBanner';

const SECTIONS = [
  { id: 'overview', label: 'Overview' },
  { id: 'distributions', label: 'Distributions' },
  { id: 'progress', label: 'Dataset Progress' },
  { id: 'timeline', label: 'Creation Timeline' },
  { id: 'detailed', label: 'Detailed Table' },
  { id: 'export', label: 'Export' },
];

export function StatisticsPage() {
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [tableSearch, setTableSearch] = useState('');

  const { data: datasets = [], isLoading: loadingList } = useQuery({
    queryKey: ['datasets'],
    queryFn: listDatasets,
    refetchInterval: autoRefresh ? 60_000 : false,
  });

  const { data: stats, isLoading: loadingStats } = useQuery({
    queryKey: statisticsQueryKey(datasets),
    queryFn: () => computeStatistics(datasets),
    staleTime: 30_000,
    enabled: datasets.length > 0,
    refetchInterval: autoRefresh ? 60_000 : false,
  });

  const overall = useMemo(() => {
    if (!stats) return null;
    const considered = stats.allQuestions.map(isValidationPassed).filter((p) => p !== null) as boolean[];
    const valRate = considered.length
      ? (considered.filter(Boolean).length / considered.length) * 100
      : 0;
    return {
      totalDatasets: datasets.length,
      totalQuestions: stats.allQuestions.length,
      completed: stats.dsStats.filter((s) => s.status === 'completed').length,
      processing: stats.dsStats.filter((s) => s.status === 'in_progress' || s.status === 'processing').length,
      avgProgress: stats.dsStats.length
        ? stats.dsStats.reduce((a, s) => a + s.progress, 0) / stats.dsStats.length
        : 0,
      valRate,
    };
  }, [stats, datasets.length]);

  const statusData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const s of stats?.dsStats ?? []) c[s.status] = (c[s.status] ?? 0) + 1;
    return toCategoryData(c);
  }, [stats]);

  const typeData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const q of stats?.allQuestions ?? []) {
      const t = questionTypeLabel(q.question_type);
      c[t] = (c[t] ?? 0) + 1;
    }
    return toCategoryData(c);
  }, [stats]);

  const provData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const q of stats?.allQuestions ?? []) {
      const raw = q.sensitivity_level ?? q.provocativeness;
      if (raw == null || String(raw).trim() === '') continue;
      c[levelLabel(raw)] = (c[levelLabel(raw)] ?? 0) + 1;
    }
    return toCategoryData(c, ['Low', 'Medium', 'High']);
  }, [stats]);

  const difficultyData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const q of stats?.allQuestions ?? []) {
      const raw = q.difficulty_level ?? q.difficulty;
      if (raw == null || String(raw).trim() === '') continue;
      c[levelLabel(raw)] = (c[levelLabel(raw)] ?? 0) + 1;
    }
    return toCategoryData(c, ['Low', 'Medium', 'High']);
  }, [stats]);

  const scoreData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const q of stats?.allQuestions ?? []) {
      const [total] = parseValidationScore(q.validation_score);
      if (total != null) {
        const bucket = String(Math.floor(total));
        c[bucket] = (c[bucket] ?? 0) + 1;
      }
    }
    return Object.entries(c)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([label, value]) => ({ label, value }));
  }, [stats]);

  const thresholdData = useMemo(() => {
    const grouped: Record<string, { passed: number; count: number }> = {};
    for (const q of stats?.allQuestions ?? []) {
      const raw = q.validation_threshold;
      if (raw == null || raw === 'N/A' || raw === '') continue;
      const thr = parseFloat(String(raw));
      if (Number.isNaN(thr)) continue;
      const key = thr.toFixed(1);
      grouped[key] ??= { passed: 0, count: 0 };
      grouped[key].count += 1;
      if (isValidationPassed(q)) grouped[key].passed += 1;
    }
    return Object.entries(grouped)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([label, { passed, count }]) => ({ label, value: count ? (passed / count) * 100 : 0 }));
  }, [stats]);

  const timelineData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const s of stats?.dsStats ?? []) {
      const d = isoDate(s.created);
      if (d) c[d] = (c[d] ?? 0) + 1;
    }
    return Object.entries(c)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([label, value]) => ({ label, value }));
  }, [stats]);

  const statsSort = useSort<DsStatRow>();
  const statsCols = useResizableColumns({ name: 240 });
  const dsStatsSorted = statsSort.sortItems(stats?.dsStats ?? [], {
    name: (s) => s.name,
    status: (s) => s.status,
    progress: (s) => s.progress,
    generated: (s) => s.generated,
    expected: (s) => s.expected,
    validation: (s) => s.validationRate,
    created: (s) => s.created,
    lastUpdated: (s) => s.lastUpdated,
  });
  const statsPaged = usePagedList(dsStatsSorted, {
    pageSize: 12,
    search: tableSearch,
    searchFields: (s) => [s.name, s.status],
  });

  if (loadingList || loadingStats) return <LoadingState label="Loading statistics…" />;

  if (!datasets.length) {
    return (
      <div>
        <PageHeader title="Statistics" subtitle="Aggregated analytics across datasets." />
        <EmptyState title="No datasets found" description="Generate datasets using Data Preprocessing." />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Statistics"
        subtitle="Aggregated analytics across datasets: question types, sensitivity, validation rates."
        actions={
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh (60s)
          </label>
        }
      />

      <nav className="mb-6 flex flex-wrap gap-2">
        {SECTIONS.map((s) => (
          <a key={s.id} href={`#${s.id}`} className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50">
            {s.label}
          </a>
        ))}
      </nav>

      {overall && (
        <div id="overview" className="grid gap-4 scroll-mt-6 sm:grid-cols-2 lg:grid-cols-5">
          <MetricCard label="Datasets" value={overall.totalDatasets} />
          <MetricCard label="Questions" value={overall.totalQuestions} />
          <MetricCard label="Completed / Processing" value={`${overall.completed} / ${overall.processing}`} />
          <MetricCard label="Avg Progress" value={`${overall.avgProgress.toFixed(1)}%`} />
          <MetricCard label="Validation Rate" value={`${overall.valRate.toFixed(1)}%`} tone="success" />
        </div>
      )}

      <section id="distributions" className="mt-8 grid scroll-mt-6 gap-6 lg:grid-cols-2">
        <PieChartRc title="Dataset Status" data={statusData} />
        <BarChartRc title="Question Types" data={typeData} color="#3f6b4f" />
        <BarChartRc title="Sensitivity / Provocativeness" data={provData} color="#8b5cf6" />
        <BarChartRc title="Difficulty" data={difficultyData} color="#f59e0b" />
        <BarChartRc title="Validation Scores" data={scoreData} color="#10b981" />
        <LineChartRc title="Success Rate by Threshold (%)" data={thresholdData} color="#0ea5e9" ydomain={[0, 100]} />
      </section>

      {stats && (
        <>
          <section id="progress" className="card mt-8 scroll-mt-6">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-semibold">Dataset Progress</h2>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="btn-secondary py-1.5 text-xs"
                  onClick={() => { statsSort.requestSort('progress'); statsPaged.setPage(1); }}
                  title="Sort by progress"
                >
                  Sort by progress{' '}
                  {statsSort.sort?.key === 'progress' ? (statsSort.sort.dir === 'asc' ? '↑' : '↓') : '↕'}
                </button>
                <input
                  type="search"
                  className="input-field max-w-xs"
                  placeholder="Filter datasets…"
                  value={tableSearch}
                  onChange={(e) => { setTableSearch(e.target.value); statsPaged.setPage(1); }}
                />
              </div>
            </div>
            <div className="space-y-4">
              {statsPaged.pageItems.map((row) => (
                <StackedProgressBar key={row.name} label={row.name} percent={row.progress} />
              ))}
            </div>
            <div className="mt-4">
              <Pagination
                page={statsPaged.page}
                totalPages={statsPaged.totalPages}
                total={statsPaged.total}
                onPageChange={statsPaged.setPage}
                label="datasets"
              />
            </div>
          </section>

          <section id="timeline" className="mt-8 scroll-mt-6">
            <LineChartRc title="Creation Timeline (datasets/day)" data={timelineData} color="#3f6b4f" />
          </section>

          <section id="detailed" className="mt-8 scroll-mt-6">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-semibold">Detailed Dataset Statistics</h2>
              <Pagination
                page={statsPaged.page}
                totalPages={statsPaged.totalPages}
                total={statsPaged.total}
                onPageChange={statsPaged.setPage}
                label="datasets"
              />
            </div>
            <div className="w-full overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="data-table" ref={statsCols.tableRef} style={statsCols.tableStyle}>
                <thead>
                  <tr>
                    <SortableTh label="Dataset" sortKey="name" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label="Status" sortKey="status" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label="Progress" sortKey="progress" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label="Generated" sortKey="generated" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label="Expected" sortKey="expected" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label="Validation %" sortKey="validation" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label="Created" sortKey="created" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label="Last Updated" sortKey="lastUpdated" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                  </tr>
                </thead>
                <tbody>
                  {statsPaged.pageItems.map((row) => (
                    <tr key={row.name}>
                      <td className="whitespace-normal break-words align-top font-medium" title={row.name}>{row.name}</td>
                      <td>{row.status}</td>
                      <td>{row.progress.toFixed(1)}%</td>
                      <td>{row.generated}</td>
                      <td>{row.expected}</td>
                      <td>{row.validationRate.toFixed(1)}%</td>
                      <td className="whitespace-nowrap text-xs text-slate-500">{truncIso(row.created)}</td>
                      <td className="whitespace-nowrap text-xs text-slate-500">{truncIso(row.lastUpdated)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section id="export" className="mt-8 flex scroll-mt-6 flex-wrap gap-3">
            <button
              type="button"
              className="btn-secondary"
              onClick={() =>
                downloadCsv(
                  'dataset_statistics.csv',
                  stats.dsStats.map((r) => ({
                    dataset_name: r.name,
                    status: r.status,
                    progress_percent: r.progress,
                    questions_generated: r.generated,
                    expected_questions: r.expected,
                    validation_rate: r.validationRate,
                    created_at: truncIso(r.created),
                    last_updated: truncIso(r.lastUpdated),
                  })),
                )
              }
            >
              Download Dataset Statistics (CSV)
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => downloadCsv('all_questions.csv', stats.allQuestions as unknown as Record<string, unknown>[])}
            >
              Download All Questions (CSV)
            </button>
          </section>
        </>
      )}
    </div>
  );
}
