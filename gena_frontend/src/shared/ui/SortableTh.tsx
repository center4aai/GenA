import { useTranslation } from 'react-i18next';
import type { SortState } from '@/shared/lib/useSort';
import type { ResizableColumns } from '@/shared/lib/useResizableColumns';

interface SortableThProps {
  label: string;
  sortKey: string;
  sort: SortState | null;
  onSort: (key: string) => void;
  className?: string;
  /** Pass a useResizableColumns() instance to make the column drag-resizable. */
  resize?: ResizableColumns;
  /** Optional tooltip explaining the column values (defaults to the label). */
  titleHint?: string;
}

export function SortableTh({ label, sortKey, sort, onSort, className, resize, titleHint }: SortableThProps) {
  const { t } = useTranslation('common');
  const active = sort?.key === sortKey;
  const arrow = !active ? '↕' : sort?.dir === 'asc' ? '↑' : '↓';
  const width = resize?.widthFor(sortKey);
  // The hint sits on the button so it covers the whole header, arrow included;
  // without one, fall back to the label so truncated headers stay readable.
  return (
    <th
      data-col={sortKey}
      className={`${resize ? 'relative overflow-hidden' : ''} ${className ?? ''}`}
      style={width != null ? { width, maxWidth: width } : undefined}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className="inline-flex max-w-full items-center gap-1 truncate hover:text-slate-800"
        title={titleHint ?? label}
      >
        <span className="truncate">{label}</span>
        <span className={active ? 'text-gena-primary' : 'text-slate-300'}>{arrow}</span>
      </button>
      {resize && (
        <span
          role="separator"
          aria-orientation="vertical"
          title={t('common:dragToResize')}
          onPointerDown={(e) => resize.startResize(sortKey, e)}
          onClick={(e) => e.stopPropagation()}
          className="absolute right-0 top-0 z-10 h-full w-2 cursor-col-resize touch-none select-none border-r border-transparent hover:border-gena-primary/60 hover:bg-gena-primary/10"
        />
      )}
    </th>
  );
}
