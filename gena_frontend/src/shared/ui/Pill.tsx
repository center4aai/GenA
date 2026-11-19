import type { ReactNode } from 'react';

export type PillTone = 'success' | 'danger' | 'warning' | 'info' | 'processing' | 'neutral';

const TONES: Record<PillTone, string> = {
  success: 'bg-emerald-100 text-emerald-800',
  danger: 'bg-red-100 text-red-800',
  warning: 'bg-amber-100 text-amber-800',
  info: 'bg-indigo-100 text-indigo-800',
  processing: 'bg-sky-100 text-sky-800',
  neutral: 'bg-slate-100 text-slate-700',
};

/** Small rounded status label (dataset status, quality verdict, edit status). */
export function Pill({
  tone = 'neutral',
  title,
  className,
  children,
}: {
  tone?: PillTone;
  title?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ${TONES[tone]} ${className ?? ''}`}
    >
      {children}
    </span>
  );
}
