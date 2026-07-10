interface PaginationProps {
  page: number;
  totalPages: number;
  total?: number;
  onPageChange: (page: number) => void;
  showInput?: boolean;
  label?: string;
}

export function Pagination({ page, totalPages, total, onPageChange, showInput = false, label = 'items' }: PaginationProps) {
  if (totalPages <= 1 && total == null) return null;
  const clamp = (p: number) => Math.min(totalPages, Math.max(1, p));

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
      {total != null && <span className="mr-1 text-slate-400">{total} {label}</span>}
      <button
        type="button"
        className="btn-secondary px-2 py-1"
        disabled={page <= 1}
        onClick={() => onPageChange(clamp(page - 1))}
      >
        Prev
      </button>
      {showInput ? (
        <span className="flex items-center gap-1">
          <input
            type="number"
            min={1}
            max={totalPages}
            value={page}
            onChange={(e) => {
              const p = parseInt(e.target.value, 10);
              if (!Number.isNaN(p)) onPageChange(clamp(p));
            }}
            className="input-field w-14 px-2 py-1 text-center"
          />
          <span className="text-slate-500">/ {totalPages}</span>
        </span>
      ) : (
        <span className="text-slate-500">
          {page} / {totalPages}
        </span>
      )}
      <button
        type="button"
        className="btn-secondary px-2 py-1"
        disabled={page >= totalPages}
        onClick={() => onPageChange(clamp(page + 1))}
      >
        Next
      </button>
    </div>
  );
}
