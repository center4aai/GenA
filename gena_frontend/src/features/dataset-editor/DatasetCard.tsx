import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { datasetEditorStatus } from '@/shared/lib/datasetVisibility';
import { formatTimestamp } from '@/shared/lib/dateTime';
import type { QuestionStats } from '@/shared/lib/questionStats';
import { versionByNumber } from '@/shared/lib/versionLabel';
import type { Dataset, DatasetDetail, DatasetVersion, DocumentKind } from '@/shared/types/documents';
import { CHUNKING_METHOD_KEYS, isChunkingMethod } from '@/shared/lib/chunkingMethods';
import { Pill } from '@/shared/ui/Pill';
import { ExportToolbar, type ExportToolbarProps } from './ExportToolbar';
import { QuestionStatsAccordion } from './QuestionStatsAccordion';
import { VersionSelect } from './VersionSelect';

const KIND_KEY: Record<DocumentKind, string> = {
  normative: 'create:kindNormative',
  textbook: 'create:kindTextbook',
  encyclopedia: 'create:kindEncyclopedia',
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-slate-800">{children}</dd>
    </div>
  );
}

/**
 * One card for the selected set: version picker, facts about the selected
 * version, exports, and the collapsed statistics accordion.
 */
export function DatasetCard({
  dataset,
  listItem,
  versions,
  version,
  onVersionChange,
  stats,
  toolbar,
  revalidation,
}: {
  dataset: DatasetDetail;
  /** The list row (carries the dataset-level status). */
  listItem?: Dataset;
  versions: DatasetVersion[];
  version: number | null;
  onVersionChange: (version: number) => void;
  stats: QuestionStats;
  toolbar: Omit<ExportToolbarProps, 'excludedCount'>;
  /** «Повторная проверка качества» accordion, when the set is ready. */
  revalidation?: ReactNode;
}) {
  const { t } = useTranslation(['results', 'common', 'create']);
  const current = versionByNumber(versions, version);
  const status = datasetEditorStatus(listItem ?? dataset);
  const kind = (listItem?.metadata?.document_kind ?? dataset.metadata?.document_kind) as DocumentKind | undefined;
  const kindLabel = kind && KIND_KEY[kind] ? t(KIND_KEY[kind]) : null;
  const meta = listItem?.metadata ?? dataset.metadata;
  const method = meta?.chunking_method;
  const methodLabel = isChunkingMethod(method) ? t(CHUNKING_METHOD_KEYS[method]) : null;
  const fallback = typeof meta?.chunking_fallback === 'string' && meta.chunking_fallback ? meta.chunking_fallback : null;
  const engine = typeof meta?.chunking_engine === 'string' && meta.chunking_engine ? meta.chunking_engine : null;
  const llmStats = meta?.chunking_stats?.llm_segmentation;
  const paragraphSource = meta?.chunking_stats?.paragraph_source;
  const llmStatsLine =
    llmStats && typeof llmStats.windows === 'number'
      ? t('create:llmSegStats', {
          windows: llmStats.windows,
          ok: llmStats.llm_ok ?? 0,
          fallback: llmStats.fallback ?? 0,
          truncated: llmStats.salvaged_truncated ?? 0,
          source:
            paragraphSource === 'layout' || paragraphSource === 'text'
              ? t(`create:paragraphSource.${paragraphSource}`)
              : t('common:none'),
        })
      : null;
  const source = dataset.source_document ?? t('common:none');
  const passRate = stats.passRate == null ? t('common:none') : `${Math.round(stats.passRate)}%`;

  return (
    <section className="card space-y-4">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 flex-1 space-y-4">
          <div>
            <h3 className="text-lg font-semibold">{dataset.name}</h3>
            {dataset.description && <p className="mt-1 text-sm text-slate-600">{dataset.description}</p>}
          </div>
          <VersionSelect versions={versions} value={version} onChange={onVersionChange} />
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <Row label={t('results:status')}>
              {status === 'processing' ? (
                <Pill tone="processing">{t('results:statusProcessing')}</Pill>
              ) : (
                <Pill tone="success">{t('results:statusCompleted')}</Pill>
              )}
            </Row>
            <Row label={t('results:sourceDocument')}>
              {kindLabel ? `${kindLabel} / ${source}` : source}
            </Row>
            {methodLabel && (
              <Row label={t('create:chunkingMethod')}>
                {methodLabel}
                {engine && (
                  <span className="ml-2 text-xs text-slate-500">{t('create:chunkingEngine', { engine })}</span>
                )}
                {fallback && (
                  <span className="ml-2 text-xs text-amber-700" title={fallback}>
                    {t('create:methodFellBack')}
                  </span>
                )}
                {llmStatsLine && <div className="mt-1 text-xs text-slate-500">{llmStatsLine}</div>}
              </Row>
            )}
            <Row label={t('results:questionsCount')}>{stats.total}</Row>
            <Row label={t('results:qualityPassRate')}>{passRate}</Row>
            <Row label={t('results:versionCreated')}>{formatTimestamp(current?.created_at) || t('common:none')}</Row>
            <Row label={t('results:versionModified')}>{formatTimestamp(current?.user_updated_at) || t('common:none')}</Row>
          </dl>
        </div>
        <ExportToolbar excludedCount={stats.excluded} {...toolbar} />
      </div>
      <QuestionStatsAccordion stats={stats} />
      {revalidation}
    </section>
  );
}
