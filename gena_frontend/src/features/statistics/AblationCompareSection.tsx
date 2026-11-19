import { useMemo, useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { getPipelineStats } from '@/shared/api/dataset';
import type { Dataset, PipelineStats } from '@/shared/types/documents';
import { BarChartRc } from '@/shared/ui/charts';

function ablationBaseName(name: string): string {
  const idx = name.lastIndexOf('__');
  if (idx <= 0) return name;
  return name.slice(0, idx);
}

function passRate(passed: number, rejected: number): number | null {
  const total = passed + rejected;
  if (!total) return null;
  return Math.round((passed / total) * 1000) / 10;
}

function validationRate(passed: number, failed: number): number | null {
  const total = passed + failed;
  if (!total) return null;
  return Math.round((passed / total) * 1000) / 10;
}

function topReason(reasons: Record<string, number>): string {
  const entries = Object.entries(reasons).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return '—';
  return `${entries[0][0]} (${entries[0][1]})`;
}

export function AblationCompareSection({ datasets }: { datasets: Dataset[] }) {
  const { t } = useTranslation(['advanced', 'progress', 'common']);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const ablationGroups = useMemo(() => {
    const groups = new Map<string, Dataset[]>();
    for (const ds of datasets) {
      const meta = ds.metadata ?? {};
      const isAblation = !!meta.ablation_testing || ds.name.includes('__');
      if (!isAblation) continue;
      const key =
        (typeof meta.experiment_source_document === 'string' && meta.experiment_source_document)
        || ablationBaseName(ds.name);
      const list = groups.get(key) ?? [];
      list.push(ds);
      groups.set(key, list);
    }
    return [...groups.entries()]
      .filter(([, members]) => members.length >= 2)
      .sort(([a], [b]) => a.localeCompare(b));
  }, [datasets]);

  const statsQueries = useQueries({
    queries: selectedIds.map((id) => ({
      queryKey: ['pipeline-stats', id],
      queryFn: () => getPipelineStats(id),
      staleTime: 30_000,
    })),
  });

  const rows = useMemo(() => {
    return selectedIds.map((id, i) => {
      const ds = datasets.find((d) => d._id === id);
      const stats = statsQueries[i]?.data as PipelineStats | undefined;
      const loading = statsQueries[i]?.isLoading || statsQueries[i]?.isFetching;
      return { id, ds, stats, loading };
    });
  }, [selectedIds, datasets, statsQueries]);

  function toggleId(id: string) {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  }

  function selectGroup(members: Dataset[]) {
    setSelectedIds(members.map((m) => m._id).filter(Boolean));
  }

  const gateChart = rows
    .filter((r) => r.stats)
    .map((r) => ({
      label: r.ds?.name ?? r.id,
      value: passRate(r.stats!.gate.passed, r.stats!.gate.rejected) ?? 0,
    }));

  const valChart = rows
    .filter((r) => r.stats)
    .map((r) => ({
      label: r.ds?.name ?? r.id,
      value: validationRate(
        r.stats!.generation.validation.passed,
        r.stats!.generation.validation.failed,
      ) ?? 0,
    }));

  return (
    <section id="compare" className="card mt-8 scroll-mt-6 space-y-4">
      <div>
        <h2 className="font-semibold">{t('advanced:compareTitle')}</h2>
        <p className="mt-1 text-sm text-slate-600">{t('advanced:compareSubtitle')}</p>
      </div>

      {ablationGroups.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <span className="text-xs font-medium text-slate-500 self-center">
            {t('advanced:ablationGroups')}:
          </span>
          {ablationGroups.map(([key, members]) => (
            <button
              key={key}
              type="button"
              className="btn-secondary py-1 text-xs"
              onClick={() => selectGroup(members)}
              title={members.map((m) => m.name).join(', ')}
            >
              {key} ({members.length})
            </button>
          ))}
        </div>
      )}

      <div className="max-h-48 overflow-y-auto rounded-lg border border-slate-200 bg-white p-2">
        {datasets.length === 0 ? (
          <p className="text-sm text-slate-500">{t('advanced:compareNoDatasets')}</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {datasets.map((ds) => (
              <li key={ds._id}>
                <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-slate-50">
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(ds._id)}
                    onChange={() => toggleId(ds._id)}
                  />
                  <span className="truncate" title={ds.name}>{ds.name}</span>
                  {ds.metadata?.pipeline_mode && (
                    <span className="ml-auto shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">
                      {String(ds.metadata.pipeline_mode)}
                    </span>
                  )}
                </label>
              </li>
            ))}
          </ul>
        )}
      </div>

      {selectedIds.length === 0 ? (
        <p className="text-sm text-slate-500">{t('advanced:compareSelectHint')}</p>
      ) : (
        <>
          <div className="w-full overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="data-table text-xs">
              <thead>
                <tr>
                  <th>{t('advanced:compareColDataset')}</th>
                  <th>{t('advanced:compareColMode')}</th>
                  <th>{t('advanced:compareColChunks')}</th>
                  <th>{t('advanced:compareColGatePass')}</th>
                  <th>{t('advanced:compareColTopRejection')}</th>
                  <th>{t('advanced:compareColAvgConf')}</th>
                  <th>{t('advanced:compareColQuestions')}</th>
                  <th>{t('advanced:compareColValPass')}</th>
                  <th>{t('advanced:compareColAvgScore')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ id, ds, stats, loading }) => {
                  if (loading && !stats) {
                    return (
                      <tr key={id}>
                        <td colSpan={9} className="text-slate-500">
                          {t('advanced:compareRowLoading', { name: ds?.name ?? id })}
                        </td>
                      </tr>
                    );
                  }
                  if (!stats) {
                    return (
                      <tr key={id}>
                        <td colSpan={9} className="text-red-600">
                          {t('advanced:compareRowFailed', { name: ds?.name ?? id })}
                        </td>
                      </tr>
                    );
                  }
                  const gatePct = passRate(stats.gate.passed, stats.gate.rejected);
                  const valPct = validationRate(
                    stats.generation.validation.passed,
                    stats.generation.validation.failed,
                  );
                  const c = stats.gate.criteria;
                  return (
                    <tr key={id}>
                      <td className="font-medium" title={ds?.name}>{ds?.name ?? id}</td>
                      <td>
                        {stats.meta.pipeline_mode ?? '—'}
                        {stats.gate.state === 'disabled' ? t('advanced:gateOffSuffix') : ''}
                      </td>
                      <td>{stats.chunking.total_chunks}</td>
                      <td>
                        {stats.gate.state === 'disabled'
                          ? t('common:na')
                          : gatePct != null
                            ? `${gatePct}% (${stats.gate.passed}/${stats.gate.passed + stats.gate.rejected})`
                            : '—'}
                      </td>
                      <td className="font-mono">{topReason(stats.gate.rejection_reasons)}</td>
                      <td>
                        {[c.c1_avg_confidence, c.c2_avg_confidence, c.c3_avg_confidence]
                          .map((v) => (v == null ? '—' : v.toFixed(2)))
                          .join(' / ')}
                      </td>
                      <td>
                        {stats.generation.questions_generated} / {stats.generation.expected_questions}
                      </td>
                      <td>{valPct != null ? `${valPct}%` : '—'}</td>
                      <td>
                        {stats.generation.validation.avg_score != null
                          ? stats.generation.validation.avg_score.toFixed(2)
                          : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <BarChartRc
              title={t('advanced:compareGatePassChart')}
              data={gateChart}
              color="#3f6b4f"
            />
            <BarChartRc
              title={t('advanced:compareValPassChart')}
              data={valChart}
              color="#0ea5e9"
            />
          </div>
        </>
      )}
    </section>
  );
}
