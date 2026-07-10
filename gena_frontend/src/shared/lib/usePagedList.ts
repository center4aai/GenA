import { useMemo, useState } from 'react';

interface PagedListOptions<T> {
  pageSize?: number;
  search?: string;
  searchFields?: (item: T) => Array<string | number | null | undefined>;
}

/**
 * Client-side filter + pagination for potentially large lists.
 * Resets to page 1 whenever the filtered length changes (e.g. new search).
 */
export function usePagedList<T>(items: T[], options: PagedListOptions<T> = {}) {
  const { pageSize = 10, search = '', searchFields } = options;
  const [page, setPage] = useState(1);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q || !searchFields) return items;
    return items.filter((item) =>
      searchFields(item).some((f) => String(f ?? '').toLowerCase().includes(q)),
    );
  }, [items, search, searchFields]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * pageSize;
  const pageItems = filtered.slice(start, start + pageSize);

  return {
    page: safePage,
    setPage,
    totalPages,
    total: filtered.length,
    start,
    pageItems,
    filteredItems: filtered,
  };
}
