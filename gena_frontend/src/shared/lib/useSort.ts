import { useState } from 'react';

export type SortDir = 'asc' | 'desc';
export interface SortState {
  key: string;
  dir: SortDir;
}

type Accessor<T> = (item: T) => string | number | null | undefined;

function compareValues(a: unknown, b: unknown): number {
  const aNil = a == null || a === '';
  const bNil = b == null || b === '';
  if (aNil && bNil) return 0;
  if (aNil) return 1; // nulls/empties always sort last
  if (bNil) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

/**
 * Header-click sorting for tables. `sortItems` applies the current sort using a
 * map of column-key -> value accessor. Unknown/absent keys leave order intact.
 */
export function useSort<T>(initial: SortState | null = null) {
  const [sort, setSort] = useState<SortState | null>(initial);

  function requestSort(key: string) {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: 'asc' };
      if (prev.dir === 'asc') return { key, dir: 'desc' };
      return null; // third click clears sorting
    });
  }

  function sortItems(items: T[], accessors: Record<string, Accessor<T>>): T[] {
    if (!sort) return items;
    const accessor = accessors[sort.key];
    if (!accessor) return items;
    const factor = sort.dir === 'asc' ? 1 : -1;
    // Stable sort: decorate with original index.
    return items
      .map((item, index) => ({ item, index }))
      .sort((x, y) => {
        const c = compareValues(accessor(x.item), accessor(y.item));
        return c !== 0 ? c * factor : x.index - y.index;
      })
      .map((d) => d.item);
  }

  return { sort, requestSort, sortItems };
}
