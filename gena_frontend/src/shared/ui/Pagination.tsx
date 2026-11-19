import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { pageRange, pageRanges } from '@/shared/lib/pagination';

interface PaginationProps {
  page: number;
  totalPages: number;
  total?: number;
  onPageChange: (page: number) => void;
  showInput?: boolean;
  /** "125 наборов" with the noun agreed to the number; defaults to the generic "items". */
  countLabel?: (total: number) => string;
  pageSize?: number;
  pageSizeOptions?: number[];
  onPageSizeChange?: (size: number) => void;
  /** Show "1–10 of 125" (needs `total` and `pageSize`) instead of "125 items". */
  rangeLabel?: boolean;
  /** Replace the page indicator with a select of item ranges (1–10 / 11–20 …). */
  rangeJump?: boolean;
}

export function Pagination({
  page,
  totalPages,
  total,
  onPageChange,
  showInput = false,
  countLabel,
  pageSize,
  pageSizeOptions = [10, 25, 50, 0],
  onPageSizeChange,
  rangeLabel = false,
  rangeJump = false,
}: PaginationProps) {
  const { t } = useTranslation('common');
  const pages = Math.max(1, totalPages);
  const clamp = (p: number) => Math.min(pages, Math.max(1, p));
  const currentPage = clamp(page);
  const [pageInput, setPageInput] = useState(String(currentPage));
  const itemsLabel = countLabel ?? ((n: number) => t('items', { count: n }));

  useEffect(() => {
    setPageInput(String(currentPage));
  }, [currentPage]);

  useEffect(() => {
    if (page !== currentPage) onPageChange(currentPage);
  }, [page, currentPage, onPageChange]);

  const commitPageInput = () => {
    const p = parseInt(pageInput, 10);
    if (Number.isNaN(p)) {
      setPageInput(String(currentPage));
      return;
    }
    const next = clamp(p);
    setPageInput(String(next));
    if (next !== currentPage) onPageChange(next);
  };

  if (total === 0 || (totalPages <= 1 && total == null)) return null;

  const canRange = total != null && pageSize != null;
  const current = canRange ? pageRange(total, pageSize, currentPage) : null;
  const ranges = canRange && rangeJump ? pageRanges(total, pageSize) : [];

  return (
    <nav aria-label={t('pagination')} className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
      {total != null && (
        <span className="mr-1 text-slate-400">
          {rangeLabel && current
            ? t('rangeOf', { from: current.from, to: current.to, total })
            : itemsLabel(total)}
        </span>
      )}
      <button
        type="button"
        className="btn-secondary px-2 py-1"
        disabled={currentPage <= 1}
        aria-label={t('prev')}
        onClick={() => onPageChange(clamp(currentPage - 1))}
      >
        {t('prev')}
      </button>
      {rangeJump && ranges.length > 0 ? (
        <select
          className="input-field w-auto py-1 pl-2 pr-6"
          value={currentPage}
          aria-label={t('jumpToRange')}
          onChange={(e) => onPageChange(clamp(parseInt(e.target.value, 10)))}
        >
          {ranges.map((r) => (
            <option key={r.page} value={r.page}>
              {r.from}–{r.to}
            </option>
          ))}
        </select>
      ) : showInput ? (
        <span className="flex items-center gap-1">
          <input
            type="number"
            min={1}
            max={pages}
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value)}
            onBlur={commitPageInput}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                commitPageInput();
              }
            }}
            aria-label={t('pageOf', { page: currentPage, pages })}
            className="input-field w-20 px-2 py-1 text-center"
          />
          <span className="text-slate-500">/ {pages}</span>
        </span>
      ) : (
        <span className="text-slate-500">
          <span aria-current="page">{currentPage}</span> / {pages}
        </span>
      )}
      <button
        type="button"
        className="btn-secondary px-2 py-1"
        disabled={currentPage >= pages}
        aria-label={t('next')}
        onClick={() => onPageChange(clamp(currentPage + 1))}
      >
        {t('next')}
      </button>
      {onPageSizeChange && pageSize != null && (
        <label className="ml-2 flex items-center gap-1 text-slate-500">
          <span className="sr-only">{t('pageSize')}</span>
          <select
            className="input-field py-1 pl-2 pr-6"
            value={pageSize}
            onChange={(e) => onPageSizeChange(parseInt(e.target.value, 10))}
          >
            {pageSizeOptions.map((n) => (
              <option key={n} value={n}>
                {n === 0 ? t('showAll') : n}
              </option>
            ))}
          </select>
        </label>
      )}
    </nav>
  );
}
