import { useCallback, useLayoutEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react';

const MIN_WIDTH = 48;

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
 * Strategy: on first layout we measure the natural (auto-layout) width of each
 * header cell (marked with `data-col`), seed those widths into state, and switch
 * the table to `table-layout: fixed`. From then on each header owns an explicit
 * width that the user can drag. Body cells that use `truncate` will simply show
 * more/less text as their column grows/shrinks.
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
    const ths = table.querySelectorAll<HTMLTableCellElement>('thead th[data-col]');
    if (!ths.length) return;
    const seed: Record<string, number> = {};
    ths.forEach((th) => {
      const key = th.dataset.col as string;
      const preferred = initialRef.current?.[key];
      const measured = Math.round(th.getBoundingClientRect().width);
      seed[key] = Math.max(MIN_WIDTH, preferred ?? measured);
    });
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
