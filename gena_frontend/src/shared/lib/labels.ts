import type { CategoryDatum } from '../ui/charts';

const QUESTION_TYPE_LABELS: Record<string, string> = {
  one: 'Single Choice',
  one_choice: 'Single Choice',
  single: 'Single Choice',
  multi: 'Multiple Choice',
  multiple: 'Multiple Choice',
  multiple_choice: 'Multiple Choice',
  open: 'Open Question',
  open_question: 'Open Question',
};

const LEVEL_LABELS: Record<string, string> = {
  '1': 'Low',
  '2': 'Medium',
  '3': 'High',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
};

export function questionTypeLabel(value: unknown): string {
  const key = String(value ?? '').trim().toLowerCase();
  return QUESTION_TYPE_LABELS[key] ?? (key ? key : 'unknown');
}

export function levelLabel(value: unknown): string {
  const key = String(value ?? '').trim().toLowerCase();
  return LEVEL_LABELS[key] ?? (key ? key : 'unknown');
}

/** Convert a {label: count} record to a sorted CategoryDatum array. */
export function toCategoryData(
  record: Record<string, number>,
  order?: string[],
): CategoryDatum[] {
  const entries = Object.entries(record).filter(([, v]) => v > 0);
  if (order) {
    entries.sort(([a], [b]) => {
      const ia = order.indexOf(a);
      const ib = order.indexOf(b);
      if (ia === -1 && ib === -1) return a.localeCompare(b);
      if (ia === -1) return 1;
      if (ib === -1) return -1;
      return ia - ib;
    });
  } else {
    entries.sort(([, a], [, b]) => b - a);
  }
  return entries.map(([label, value]) => ({ label, value }));
}

/** ISO timestamp truncated to seconds (matches Python str(dt)[:19]). */
export function truncIso(value: unknown): string {
  const s = String(value ?? '');
  return s ? s.slice(0, 19).replace('T', ' ') : '';
}

/** Date portion of an ISO timestamp (YYYY-MM-DD). */
export function isoDate(value: unknown): string {
  const s = String(value ?? '');
  return s ? s.slice(0, 10) : '';
}
