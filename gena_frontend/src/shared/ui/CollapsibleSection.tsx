import type { ReactNode } from 'react';

/** Expandable teal panel used under the pipeline stepper for stage details. */
export type CollapsibleTone = 'teal' | 'green' | 'yellow' | 'blue' | 'red';

const TONE_CLASSES: Record<CollapsibleTone, string> = {
  teal: 'border-teal-200/80 bg-teal-50/70 open:bg-teal-50',
  green: 'border-emerald-200 bg-emerald-50/80 open:bg-emerald-50',
  yellow: 'border-amber-200 bg-amber-50/80 open:bg-amber-50',
  blue: 'border-sky-200 bg-sky-50/80 open:bg-sky-50',
  red: 'border-red-200 bg-red-50/80 open:bg-red-50',
};

export function CollapsibleSection({
  title,
  children,
  defaultOpen = false,
  onToggle,
  tone = 'teal',
}: {
  title: ReactNode;
  children?: ReactNode;
  defaultOpen?: boolean;
  onToggle?: (open: boolean) => void;
  /** Background by status (light green/yellow/blue/red); teal by default. */
  tone?: CollapsibleTone;
}) {
  return (
    <details
      className={`group overflow-hidden rounded-xl border ${TONE_CLASSES[tone]}`}
      open={defaultOpen || undefined}
      onToggle={(e) => onToggle?.((e.target as HTMLDetailsElement).open)}
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium text-teal-900 [&::-webkit-details-marker]:hidden">
        {/* The same ▸ the sidebar uses; turns to point down while the panel is open. */}
        <span
          className="inline-block select-none text-teal-700 transition-transform duration-200 group-open:rotate-90"
          aria-hidden
        >
          ▸
        </span>
        <span className="text-gena-primary hover:underline">{title}</span>
      </summary>
      {children != null && (
        <div className="border-t border-teal-100 bg-white/70 px-4 py-3">{children}</div>
      )}
    </details>
  );
}
