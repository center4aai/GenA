import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { listDatasets } from '@/shared/api/dataset';
import { isValidationPassed, parseValidationScore } from '@/shared/lib/progress';
import { downloadCsv } from '@/shared/lib/extendedExport';
import { formatTimestamp, formatTimestampDay, timestampMs } from '@/shared/lib/dateTime';
import { questionTypeLabel, levelLabel, datasetStatusLabel, toCategoryData } from '@/shared/lib/labels';
import { computeStatistics, statisticsQueryKey, type DsStatRow } from './statisticsData';
import { AblationCompareSection } from './AblationCompareSection';
import { usePagedList } from '@/shared/lib/usePagedList';
import { useSort } from '@/shared/lib/useSort';
import { useResizableColumns } from '@/shared/lib/useResizableColumns';
import { usePageTitle } from '@/shared/i18n/usePageTitle';
import { PageHeader } from '@/shared/ui/PageHeader';
import { MetricCard } from '@/shared/ui/MetricCard';
import { Pagination } from '@/shared/ui/Pagination';
import { SortableTh } from '@/shared/ui/SortableTh';
import { BarChartRc, PieChartRc, LineChartRc, StackedProgressBar } from '@/shared/ui/charts';
import { LoadingState, EmptyState, StatusBanner } from '@/shared/ui/StatusBanner';

const SECTIONS = [
  { id: 'overview', labelKey: 'tabOverview' },
  { id: 'distributions', labelKey: 'tabDistributions' },
  { id: 'progress', labelKey: 'tabProgress' },
  { id: 'compare', labelKey: 'tabCompare' },
  { id: 'timeline', labelKey: 'tabTimeline' },
  { id: 'detailed', labelKey: 'tabDetailed' },
  { id: 'export', labelKey: 'tabExport' },
] as const;

export function StatisticsPage() {
  const { t } = useTranslation(['advanced', 'nav', 'common', 'progress', 'create', 'results']);
  usePageTitle('analytics', 'nav');
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [tableSearch, setTableSearch] = useState('');

  const { data: datasets = [], isLoading: loadingList, isError: listError } = useQuery({
    queryKey: ['datasets'],
    queryFn: () => listDatasets(),
    refetchInterval: autoRefresh ? 60_000 : false,
  });

  const {
    data: stats,
    isLoading: loadingStats,
    isError: statsError,
    error: statsQueryError,
  } = useQuery({
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
      totalDatasets: stats.dsStats.length,
      totalQuestions: stats.allQuestions.length,
      completed: stats.dsStats.filter((s) => s.status === 'completed' || s.status === 'completed_with_failures').length,
      processing: stats.dsStats.filter((s) => s.status === 'in_progress' || s.status === 'processing').length,
      avgProgress: stats.dsStats.length
        ? stats.dsStats.reduce((a, s) => a + s.progress, 0) / stats.dsStats.length
        : 0,
      valRate,
    };
  }, [stats]);

  const statusData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const s of stats?.dsStats ?? []) {
      const label = datasetStatusLabel(s.status, t);
      c[label] = (c[label] ?? 0) + 1;
    }
    return toCategoryData(c);
  }, [stats, t]);

  const typeData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const q of stats?.allQuestions ?? []) {
      const label = questionTypeLabel(q.question_type, t);
      c[label] = (c[label] ?? 0) + 1;
    }
    return toCategoryData(c);
  }, [stats, t]);

  const levelOrder = useMemo(
    () => [levelLabel('low', t), levelLabel('medium', t), levelLabel('high', t)],
    [t],
  );

  const provData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const q of stats?.allQuestions ?? []) {
      const raw = q.sensitivity_level ?? q.provocativeness;
      if (raw == null || String(raw).trim() === '') continue;
      const label = levelLabel(raw, t);
      c[label] = (c[label] ?? 0) + 1;
    }
    return toCategoryData(c, levelOrder);
  }, [stats, t, levelOrder]);

  const difficultyData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const q of stats?.allQuestions ?? []) {
      const raw = q.difficulty_level ?? q.difficulty;
      if (raw == null || String(raw).trim() === '') continue;
      const label = levelLabel(raw, t);
      c[label] = (c[label] ?? 0) + 1;
    }
    return toCategoryData(c, levelOrder);
  }, [stats, t, levelOrder]);

  const scoreData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const q of stats?.allQuestions ?? []) {
      const [total] = parseValidationScore(q.validation_score);
      if (total != null) {
        const bucket = String(total);
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
      const thr = Number.parseInt(String(raw), 10);
      if (Number.isNaN(thr)) continue;
      const key = String(thr);
      const passed = isValidationPassed(q);
      if (passed === null) continue;
      grouped[key] ??= { passed: 0, count: 0 };
      grouped[key].count += 1;
      if (passed) grouped[key].passed += 1;
    }
    return Object.entries(grouped)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([label, { passed, count }]) => ({ label, value: count ? (passed / count) * 100 : 0 }));
  }, [stats]);

  const timelineData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const s of stats?.dsStats ?? []) {
      const d = formatTimestampDay(s.created);
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
    created: (s) => timestampMs(s.created),
    lastUpdated: (s) => timestampMs(s.lastUpdated),
  });
  const statsPaged = usePagedList(dsStatsSorted, {
    pageSize: 12,
    search: tableSearch,
    searchFields: (s) => [s.name, s.status, datasetStatusLabel(s.status, t)],
  });

  if (loadingList || loadingStats) return <LoadingState label={t('advanced:loadingStatistics')} />;

  if (listError && !datasets.length) {
    return (
      <div>
        <PageHeader title={t('advanced:analyticsTitle')} subtitle={t('advanced:analyticsSubtitle')} />
        <StatusBanner tone="error">{t('advanced:loadDatasetsFailed')}</StatusBanner>
      </div>
    );
  }

  if (!datasets.length) {
    return (
      <div>
        <PageHeader title={t('advanced:analyticsTitle')} subtitle={t('advanced:analyticsSubtitle')} />
        <EmptyState title={t('advanced:noDatasetsFound')} description={t('advanced:noDatasetsHint')} />
      </div>
    );
  }

  if (statsError && !stats) {
    return (
      <div>
        <PageHeader title={t('advanced:analyticsTitle')} subtitle={t('advanced:analyticsSubtitle')} />
        <StatusBanner tone="error">
          {t('advanced:loadStatsFailed', {
            error: statsQueryError instanceof Error ? statsQueryError.message : t('errors:generic'),
          })}
        </StatusBanner>
      </div>
    );
  }

  if (!stats || stats.dsStats.length === 0) {
    return (
      <div>
        <PageHeader title={t('advanced:analyticsTitle')} subtitle={t('advanced:analyticsSubtitle')} />
        <EmptyState title={t('advanced:noStatsTitle')} description={t('advanced:noStatsDesc')} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title={t('advanced:analyticsTitle')}
        subtitle={t('advanced:analyticsSubtitle')}
        actions={
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            {t('progress:autoRefresh')}
          </label>
        }
      />

      <nav className="mb-6 flex flex-wrap gap-2">
        {SECTIONS.map((s) => (
          <a key={s.id} href={`#${s.id}`} className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50">
            {t(`advanced:${s.labelKey}`)}
          </a>
        ))}
      </nav>

      {overall && (
        <div id="overview" className="grid gap-4 scroll-mt-6 sm:grid-cols-2 lg:grid-cols-5">
          <MetricCard label={t('advanced:metricDatasets')} value={overall.totalDatasets} />
          <MetricCard label={t('advanced:metricQuestions')} value={overall.totalQuestions} />
          <MetricCard label={t('advanced:metricCompletedProcessing')} value={`${overall.completed} / ${overall.processing}`} />
          <MetricCard label={t('advanced:metricAvgProgress')} value={`${overall.avgProgress.toFixed(1)}%`} />
          <MetricCard label={t('advanced:metricValidationRate')} value={`${overall.valRate.toFixed(1)}%`} tone="success" />
        </div>
      )}

      <section id="distributions" className="mt-8 grid scroll-mt-6 gap-6 lg:grid-cols-2">
        <PieChartRc title={t('advanced:chartDatasetStatus')} data={statusData} />
        <BarChartRc title={t('advanced:chartQuestionTypes')} data={typeData} color="#3f6b4f" />
        <BarChartRc title={t('advanced:chartSensitivity')} data={provData} color="#8b5cf6" />
        <BarChartRc title={t('advanced:chartDifficulty')} data={difficultyData} color="#f59e0b" />
        <BarChartRc title={t('advanced:chartValidationScores')} data={scoreData} color="#10b981" />
        <LineChartRc title={t('advanced:chartSuccessByThreshold')} data={thresholdData} color="#0ea5e9" ydomain={[0, 100]} />
      </section>

      {stats && (
        <>
          <section id="progress" className="card mt-8 scroll-mt-6">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-semibold">{t('advanced:tabProgress')}</h2>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="btn-secondary py-1.5 text-xs"
                  onClick={() => { statsSort.requestSort('progress'); statsPaged.setPage(1); }}
                  title={t('advanced:sortByProgress')}
                >
                  {t('advanced:sortByProgress')}{' '}
                  {statsSort.sort?.key === 'progress' ? (statsSort.sort.dir === 'asc' ? '↑' : '↓') : '↕'}
                </button>
                <input
                  type="search"
                  className="input-field max-w-xs"
                  placeholder={t('advanced:filterDatasets')}
                  value={tableSearch}
                  onChange={(e) => { setTableSearch(e.target.value); statsPaged.setPage(1); }}
                />
              </div>
            </div>
            <div className="space-y-4">
              {statsPaged.pageItems.map((row) => (
                <StackedProgressBar key={row.id} label={row.name} percent={row.progress} />
              ))}
            </div>
            <div className="mt-4">
              <Pagination
                page={statsPaged.page}
                totalPages={statsPaged.totalPages}
                total={statsPaged.total}
                onPageChange={statsPaged.setPage}
                countLabel={(n) => t('common:datasets', { count: n })}
              />
            </div>
          </section>

          <AblationCompareSection datasets={datasets} />

          <section id="timeline" className="mt-8 scroll-mt-6">
            <LineChartRc title={t('advanced:chartCreationTimeline')} data={timelineData} color="#3f6b4f" />
          </section>

          <section id="detailed" className="mt-8 scroll-mt-6">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-semibold">{t('advanced:detailedStats')}</h2>
              <Pagination
                page={statsPaged.page}
                totalPages={statsPaged.totalPages}
                total={statsPaged.total}
                onPageChange={statsPaged.setPage}
                countLabel={(n) => t('common:datasets', { count: n })}
              />
            </div>
            <div className="w-full overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="data-table" ref={statsCols.tableRef} style={statsCols.tableStyle}>
                <thead>
                  <tr>
                    <SortableTh label={t('advanced:colDataset')} sortKey="name" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label={t('advanced:colStatus')} sortKey="status" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label={t('advanced:colProgress')} sortKey="progress" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label={t('advanced:colGenerated')} sortKey="generated" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label={t('advanced:colExpected')} sortKey="expected" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label={t('advanced:colValidationPct')} sortKey="validation" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label={t('advanced:colCreated')} sortKey="created" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                    <SortableTh label={t('advanced:colLastUpdated')} sortKey="lastUpdated" sort={statsSort.sort} onSort={statsSort.requestSort} resize={statsCols} />
                  </tr>
                </thead>
                <tbody>
                  {statsPaged.pageItems.map((row) => (
                    <tr key={row.id}>
                      <td className="font-medium" title={row.name}>{row.name}</td>
                      <td>{datasetStatusLabel(row.status, t)}</td>
                      <td>{row.progress.toFixed(1)}%</td>
                      <td>{row.generated}</td>
                      <td>{row.expected}</td>
                      <td>{row.validationRate.toFixed(1)}%</td>
                      <td className="whitespace-nowrap text-slate-500">{formatTimestamp(row.created)}</td>
                      <td className="whitespace-nowrap text-slate-500">{formatTimestamp(row.lastUpdated)}</td>
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
                    created_at: formatTimestamp(r.created),
                    last_updated: formatTimestamp(r.lastUpdated),
                  })),
                )
              }
            >
              {t('advanced:downloadDatasetStatsCsv')}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => downloadCsv('all_questions.csv', stats.allQuestions as unknown as Record<string, unknown>[])}
            >
              {t('advanced:downloadAllQuestionsCsv')}
            </button>
          </section>
        </>
      )}
    </div>
  );
}
