import { useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { QuestionFilterState } from '@/shared/lib/questionFilters';
import { EmptyState } from '@/shared/ui/StatusBanner';
import { Pagination } from '@/shared/ui/Pagination';
import { QuestionFilters } from './QuestionFilters';

export function QuestionsSection({
  total,
  totalUnfiltered,
  unsavedCount,
  onShowUnsaved,
  pendingFilters,
  appliedFilters,
  chunkOptions,
  onPendingFiltersChange,
  onApplyFilters,
  onResetFilters,
  page,
  totalPages,
  pageSize,
  onPageChange,
  onPageSizeChange,
  readOnly,
  lockedMessage,
  children,
}: {
  /** Questions matching the applied filters. */
  total: number;
  /** Questions in the version before filtering. */
  totalUnfiltered: number;
  unsavedCount: number;
  onShowUnsaved: () => void;
  pendingFilters: QuestionFilterState;
  appliedFilters: QuestionFilterState;
  chunkOptions: string[];
  onPendingFiltersChange: (next: QuestionFilterState) => void;
  onApplyFilters: () => void;
  onResetFilters: () => void;
  page: number;
  totalPages: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  readOnly: boolean;
  /** Shown next to the heading while editing is locked (quality revalidation). */
  lockedMessage?: string | null;
  children: ReactNode;
}) {
  const { t } = useTranslation(['results', 'common']);
  // "Наверх" at the foot of a long page scrolls back to this heading; the app
  // scrolls inside <main>, so scrollIntoView rather than window.scrollTo.
  const topRef = useRef<HTMLElement>(null);
  const scrollToTop = () => topRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const pagination = (
    <Pagination
      page={page}
      totalPages={totalPages}
      total={total}
      onPageChange={onPageChange}
      pageSize={pageSize}
      onPageSizeChange={onPageSizeChange}
      rangeLabel
      rangeJump
    />
  );

  return (
    <section ref={topRef} className="scroll-mt-4 space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-semibold">{t('results:questionsHeading')}</h2>
        {unsavedCount > 0 && (
          <button
            type="button"
            className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800 hover:bg-amber-200"
            title={t('results:unsavedFilterHint')}
            onClick={onShowUnsaved}
          >
            {t('results:unsaved', { count: unsavedCount })} →
          </button>
        )}
        {lockedMessage && (
          <span className="rounded-full bg-sky-100 px-2.5 py-0.5 text-xs font-medium text-sky-800">
            {lockedMessage}
          </span>
        )}
      </div>
      <QuestionFilters
        pending={pendingFilters}
        applied={appliedFilters}
        chunkOptions={chunkOptions}
        onPendingChange={onPendingFiltersChange}
        onApply={onApplyFilters}
        onReset={onResetFilters}
      />
      {total === 0 ? (
        <EmptyState title={totalUnfiltered === 0 ? t('results:noQuestions') : t('results:noFilterMatches')} />
      ) : (
        <>
          {pagination}
          <div className="space-y-4">{children}</div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            {pagination}
            <button type="button" className="btn-secondary py-1 text-xs" onClick={scrollToTop}>
              ↑ {t('results:backToTop')}
            </button>
          </div>
        </>
      )}
      {readOnly && <p className="text-sm text-slate-500">{t('results:readOnly')}</p>}
    </section>
  );
}
