import { useTranslation } from 'react-i18next';
import {
  datasetEditorStatus,
  datasetQuestionCount,
} from '@/shared/lib/datasetVisibility';
import { formatTimestamp, timestampMs } from '@/shared/lib/dateTime';
import { usePagedList } from '@/shared/lib/usePagedList';
import { useResizableColumns } from '@/shared/lib/useResizableColumns';
import { useSort } from '@/shared/lib/useSort';
import type { Dataset } from '@/shared/types/documents';
import { Pagination } from '@/shared/ui/Pagination';
import { Pill } from '@/shared/ui/Pill';
import { SortableTh } from '@/shared/ui/SortableTh';

const PAGE_SIZE = 8;

/**
 * "Доступные наборы вопросов": one row per set, no version rows. Mirrors the
 * generations table on the progress page (sortable, resizable, paged).
 */
export function DatasetsTable({
  datasets,
  activeId,
  onSelect,
  mineOnly,
  onMineOnlyChange,
  search,
  onSearchChange,
}: {
  datasets: Dataset[];
  activeId: string | null;
  onSelect: (id: string) => void;
  mineOnly: boolean;
  onMineOnlyChange: (mine: boolean) => void;
  search: string;
  onSearchChange: (value: string) => void;
}) {
  const { t } = useTranslation(['results', 'common']);
  const sort = useSort<Dataset>({ key: 'created', dir: 'desc' });
  const cols = useResizableColumns({ name: 320 });

  const sorted = sort.sortItems(datasets, {
    name: (d) => d.name,
    status: (d) => datasetEditorStatus(d),
    questions: (d) => datasetQuestionCount(d),
    versions: (d) => d.versions_count ?? d.current_version ?? 0,
    created: (d) => timestampMs(d.created_at),
    modified: (d) => timestampMs(d.last_user_modified_at),
  });
  const paged = usePagedList(sorted, {
    pageSize: PAGE_SIZE,
    search,
    searchFields: (d) => [d.name, d.source_document],
  });

  const statusPill = (d: Dataset) =>
    datasetEditorStatus(d) === 'processing' ? (
      <Pill tone="processing">{t('results:statusProcessing')}</Pill>
    ) : (
      <Pill tone="success">{t('results:statusCompleted')}</Pill>
    );

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold">{t('results:availableDatasets')}</h2>
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="search"
            className="input-field w-64"
            placeholder={t('common:search')}
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
          />
          <div className="inline-flex overflow-hidden rounded-lg border border-slate-300 text-sm">
            <button
              type="button"
              className={`px-3 py-1.5 ${mineOnly ? 'bg-gena-primary text-white' : 'bg-white text-slate-700'}`}
              onClick={() => onMineOnlyChange(true)}
            >
              {t('results:mine')}
            </button>
            <button
              type="button"
              className={`px-3 py-1.5 ${!mineOnly ? 'bg-gena-primary text-white' : 'bg-white text-slate-700'}`}
              onClick={() => onMineOnlyChange(false)}
            >
              {t('results:all')}
            </button>
          </div>
        </div>
      </div>
      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <table className="data-table" ref={cols.tableRef} style={cols.tableStyle}>
          <thead>
            <tr>
              <SortableTh label={t('results:colDataset')} sortKey="name" sort={sort.sort} onSort={sort.requestSort} resize={cols} />
              <SortableTh label={t('results:colStatus')} sortKey="status" sort={sort.sort} onSort={sort.requestSort} resize={cols} />
              <SortableTh label={t('results:colQuestions')} sortKey="questions" sort={sort.sort} onSort={sort.requestSort} resize={cols} />
              <SortableTh label={t('results:colVersions')} sortKey="versions" sort={sort.sort} onSort={sort.requestSort} resize={cols} />
              <SortableTh label={t('results:colCreated')} sortKey="created" sort={sort.sort} onSort={sort.requestSort} resize={cols} />
              <SortableTh label={t('results:colModified')} sortKey="modified" sort={sort.sort} onSort={sort.requestSort} resize={cols} />
            </tr>
          </thead>
          <tbody>
            {paged.pageItems.map((d) => (
              <tr
                key={d._id}
                className={`cursor-pointer ${d._id === activeId ? 'bg-gena-primary/10' : 'hover:bg-slate-50'}`}
                onClick={() => onSelect(d._id)}
              >
                <td className="font-medium">{d.name}</td>
                <td>{statusPill(d)}</td>
                <td className="tabular-nums">{datasetQuestionCount(d)}</td>
                <td className="tabular-nums">{d.versions_count ?? d.current_version ?? t('common:none')}</td>
                <td className="whitespace-nowrap tabular-nums">{formatTimestamp(d.created_at)}</td>
                <td className="whitespace-nowrap tabular-nums">{formatTimestamp(d.last_user_modified_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination
        page={paged.page}
        totalPages={paged.totalPages}
        total={paged.total}
        onPageChange={paged.setPage}
        countLabel={(n) => t('common:datasets', { count: n })}
      />
    </section>
  );
}
