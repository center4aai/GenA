import { describe, expect, it } from 'vitest';
import { pageRange, pageRanges } from './pagination';

describe('pageRanges', () => {
  it('splits a list into inclusive 1-based ranges', () => {
    expect(pageRanges(25, 10)).toEqual([
      { page: 1, from: 1, to: 10 },
      { page: 2, from: 11, to: 20 },
      { page: 3, from: 21, to: 25 },
    ]);
  });

  it('treats page size 0 as a single page and empty lists as no pages', () => {
    expect(pageRanges(7, 0)).toEqual([{ page: 1, from: 1, to: 7 }]);
    expect(pageRanges(0, 10)).toEqual([]);
  });

  it('clamps the requested page into the available range', () => {
    expect(pageRange(25, 10, 3)).toEqual({ page: 3, from: 21, to: 25 });
    expect(pageRange(25, 10, 9)).toEqual({ page: 3, from: 21, to: 25 });
    expect(pageRange(25, 10, 0)).toEqual({ page: 1, from: 1, to: 10 });
    expect(pageRange(0, 10, 1)).toBeNull();
  });
});
