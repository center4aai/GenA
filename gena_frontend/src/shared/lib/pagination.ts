export interface PageRange {
  page: number;
  /** 1-based, inclusive */
  from: number;
  to: number;
}

/** "1–10 / 11–20 / …" ranges for a quick-jump select; pageSize 0 means one page with everything. */
export function pageRanges(total: number, pageSize: number): PageRange[] {
  if (total <= 0) return [];
  const size = pageSize > 0 ? pageSize : total;
  const ranges: PageRange[] = [];
  for (let page = 1, from = 1; from <= total; page += 1, from += size) {
    ranges.push({ page, from, to: Math.min(total, from + size - 1) });
  }
  return ranges;
}

/** The visible slice for one page, or null when the list is empty. */
export function pageRange(total: number, pageSize: number, page: number): PageRange | null {
  const ranges = pageRanges(total, pageSize);
  if (!ranges.length) return null;
  return ranges[Math.min(Math.max(page, 1), ranges.length) - 1];
}
