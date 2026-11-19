import { describe, expect, it } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePagedList } from './usePagedList';

interface Item {
  id: number;
  name: string;
}

const ITEMS: Item[] = Array.from({ length: 25 }, (_, i) => ({
  id: i + 1,
  name: `item-${i + 1}`,
}));

describe('usePagedList', () => {
  it('paginates with default page size', () => {
    const { result } = renderHook(() => usePagedList(ITEMS));
    expect(result.current.pageItems).toHaveLength(10);
    expect(result.current.totalPages).toBe(3);
    expect(result.current.total).toBe(25);
  });

  it('filters by search fields', () => {
    const items = [
      { id: 1, name: 'alpha-one' },
      { id: 2, name: 'beta-two' },
      { id: 3, name: 'alpha-three' },
    ];
    const { result } = renderHook(() =>
      usePagedList(items, {
        search: 'alpha',
        searchFields: (item) => [item.name],
      }),
    );
    expect(result.current.total).toBe(2);
    expect(result.current.filteredItems.every((i) => i.name.includes('alpha'))).toBe(true);
  });

  it('clamps page when filtered results shrink', () => {
    const { result, rerender } = renderHook(
      ({ search }: { search: string }) =>
        usePagedList(ITEMS, {
          pageSize: 10,
          search,
          searchFields: (item: Item) => [item.name],
        }),
      { initialProps: { search: '' } },
    );

    act(() => {
      result.current.setPage(3);
    });
    expect(result.current.page).toBe(3);

    rerender({ search: 'item-1' });
    expect(result.current.page).toBeLessThanOrEqual(result.current.totalPages);
    expect(result.current.pageItems.length).toBeGreaterThan(0);
  });
});
