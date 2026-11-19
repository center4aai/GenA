import { useEffect, useRef } from 'react';

export interface MultiSelectOption {
  value: string;
  label: string;
}

interface MultiSelectProps {
  label: string;
  options: MultiSelectOption[];
  /** Selected values; an empty set means "all". */
  value: Set<string>;
  onChange: (next: Set<string>) => void;
  /** Text of the pseudo-option that clears the selection ("Все"). */
  allLabel: string;
  /** Summary text when some values are selected, e.g. "3 выбрано". */
  countLabel: (n: number) => string;
  disabled?: boolean;
}

/**
 * Checkbox dropdown for filters. Built on <details> so it needs no portal or
 * outside-click handling; the "all" row is a shortcut for clearing the set.
 */
export function MultiSelect({ label, options, value, onChange, allLabel, countLabel, disabled }: MultiSelectProps) {
  const summary = value.size === 0 ? allLabel : countLabel(value.size);
  const ref = useRef<HTMLDetailsElement>(null);

  // A native <details> only closes from its own summary, so an open list would
  // sit over the Apply button until clicked again. Close it on outside click
  // and on Escape like a regular dropdown.
  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      const el = ref.current;
      if (el?.open && !el.contains(event.target as Node)) el.open = false;
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && ref.current?.open) ref.current.open = false;
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, []);

  function toggle(v: string) {
    const next = new Set(value);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    onChange(next);
  }

  return (
    <details ref={ref} className={`group relative text-sm ${disabled ? 'pointer-events-none opacity-60' : ''}`}>
      <summary className="input-field flex cursor-pointer list-none items-center justify-between gap-2 py-1.5 [&::-webkit-details-marker]:hidden">
        <span className="truncate">
          <span className="text-slate-500">{label}: </span>
          <span className="font-medium text-slate-800">{summary}</span>
        </span>
        <span aria-hidden className="inline-block text-slate-400 transition-transform duration-200 group-open:rotate-180">
          ▾
        </span>
      </summary>
      {/* Sized to its longest label (capped) so options are not cut to the control's width. */}
      <div className="absolute z-20 mt-1 max-h-64 w-max min-w-full max-w-xs overflow-auto rounded-lg border border-slate-200 bg-white p-2 shadow-lg">
        <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-slate-50">
          <input type="checkbox" checked={value.size === 0} onChange={() => onChange(new Set())} />
          <span>{allLabel}</span>
        </label>
        {options.map((o) => (
          <label key={o.value} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-slate-50">
            <input type="checkbox" checked={value.has(o.value)} onChange={() => toggle(o.value)} />
            <span className="truncate">{o.label}</span>
          </label>
        ))}
      </div>
    </details>
  );
}
