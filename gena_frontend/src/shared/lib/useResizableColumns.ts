import { useCallback, useLayoutEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react';

const MIN_WIDTH = 48;
/** Action columns must fit a button label; below this they collapse. */
const FIXED_COL_MIN_WIDTH = 104;

export interface ResizableColumns {
  /** Attach to the <table> element. */
  tableRef: (el: HTMLTableElement | null) => void;
  /** Spread onto the <table> element (enables fixed layout once seeded). */
  tableStyle: CSSProperties | undefined;
  /** Current width for a column key (px), or undefined before seeding. */
  widthFor: (key: string) => number | undefined;
  /** Begin a drag-resize for a column from its right-edge handle. */
  startResize: (key: string, event: ReactPointerEvent) => void;
}

/**
 * Lets the user drag column borders to resize table columns.
 *
 * Strategy: on first layout we measure every header cell. Columns marked with
 * `data-col` are seeded into state (preferring `initialWidths` when provided);
 * headers without `data-col` reserve their natural width so they are not
 * squeezed when we scale sized columns to fit the container. Then we switch
 * the table to `table-layout: fixed`. From then on each sized header owns an
 * explicit width that the user can drag. Body cells that use `truncate` will
 * simply show more/less text as their column grows/shrinks.
 */
/**
 * @param initialWidths Optional preferred starting widths (px) keyed by column.
 *   These win over the measured natural width, so long-text columns (names,
 *   descriptions, ids) can start comfortably wide instead of collapsed.
 */
export function useResizableColumns(initialWidths?: Record<string, number>): ResizableColumns {
  const tableEl = useRef<HTMLTableElement | null>(null);
  const [widths, setWidths] = useState<Record<string, number>>({});
  const seeded = useRef(false);
  const initialRef = useRef(initialWidths);

  useLayoutEffect(() => {
    if (seeded.current) return;
    const table = tableEl.current;
    if (!table) return;
    const ths = table.querySelectorAll<HTMLTableCellElement>('thead th');
    if (!ths.length) return;
    const firstRow = table.querySelector<HTMLTableRowElement>('tbody tr');
    const seed: Record<string, number> = {};
    let reserved = 0;
    ths.forEach((th, index) => {
      const key = th.dataset.col;
      const measured = Math.round(th.getBoundingClientRect().width);
      if (!key) {
        // Header cells without data-col are fixed (action columns). Under
        // `table-layout: fixed` a column with no width shares whatever is left
        // over, which squeezed the buttons into a vertical stack of letters —
        // so pin an explicit width wide enough for the header and its content.
        const cell = firstRow?.children[index] as HTMLElement | undefined;
        const contentWidth = cell ? Math.round(cell.getBoundingClientRect().width) : 0;
        const width = Math.max(measured, contentWidth, FIXED_COL_MIN_WIDTH);
        th.style.width = `${width}px`;
        reserved += width;
        return;
      }
      const preferred = initialRef.current?.[key];
      seed[key] = Math.max(MIN_WIDTH, preferred ?? measured);
    });
    if (!Object.keys(seed).length) return;
    // Keep the initial layout inside the visible window: if the seeded widths
    // would be wider than the remaining budget (container minus reserved
    // non-data-col headers), scale them down proportionally so the table fits
    // and long cell text wraps onto multiple lines instead of forcing
    // horizontal overflow. Users can still drag a column wider.
    const containerWidth = (table.parentElement?.clientWidth ?? table.clientWidth) || 0;
    const budget = Math.max(0, containerWidth - reserved);
    const total = Object.values(seed).reduce((a, b) => a + b, 0);
    if (budget > 0 && total > budget) {
      const scale = budget / total;
      for (const key of Object.keys(seed)) {
        seed[key] = Math.max(MIN_WIDTH, Math.floor(seed[key] * scale));
      }
    }
    seeded.current = true;
    setWidths(seed);
  });

  function startResize(key: string, event: ReactPointerEvent) {
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const th = (event.currentTarget as HTMLElement).closest('th');
    const startW = widths[key] ?? (th ? th.getBoundingClientRect().width : MIN_WIDTH);

    const onMove = (ev: PointerEvent) => {
      const next = Math.max(MIN_WIDTH, Math.round(startW + (ev.clientX - startX)));
      setWidths((w) => ({ ...w, [key]: next }));
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }

  const tableRef = useCallback((el: HTMLTableElement | null) => {
    tableEl.current = el;
  }, []);

  return {
    tableRef,
    tableStyle: seeded.current ? { tableLayout: 'fixed' } : undefined,
    widthFor: (key) => widths[key],
    startResize,
  };
}
