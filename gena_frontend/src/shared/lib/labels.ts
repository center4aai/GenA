import type { CategoryDatum } from '../ui/charts';
import i18n from '@/shared/i18n';

export type Translate = (key: string, opts?: Record<string, unknown>) => string;

const QUESTION_TYPE_KEYS: Record<string, string> = {
  one: 'create:typeOne',
  one_choice: 'create:typeOne',
  single: 'create:typeOne',
  multi: 'create:typeMulti',
  multiple: 'create:typeMulti',
  multiple_choice: 'create:typeMulti',
  open: 'create:typeOpen',
  open_question: 'create:typeOpen',
};

const LEVEL_KEYS: Record<string, string> = {
  '1': 'results:levelLow',
  '2': 'results:levelMedium',
  '3': 'results:levelHigh',
  low: 'results:levelLow',
  medium: 'results:levelMedium',
  high: 'results:levelHigh',
};

function resolveT(t?: Translate): Translate {
  if (t) return t;
  return (key, opts) => String(i18n.t(key, opts as never));
}

export function questionTypeLabel(value: unknown, t?: Translate): string {
  const key = String(value ?? '').trim().toLowerCase();
  const i18nKey = QUESTION_TYPE_KEYS[key];
  if (i18nKey) return resolveT(t)(i18nKey);
  return key || 'unknown';
}

export function levelLabel(value: unknown, t?: Translate): string {
  const key = String(value ?? '').trim().toLowerCase();
  const i18nKey = LEVEL_KEYS[key];
  if (i18nKey) return resolveT(t)(i18nKey);
  return key || 'unknown';
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

/**
 * Human-readable queue name: strip leading `queue_` and trailing
 * `_YYYYMMDDHHMMSS` (and repeated timestamp suffixes). Full technical name
 * should still be available via tooltip/`title`.
 */
export function humanQueueName(name: unknown): string {
  let s = String(name ?? '').trim();
  if (!s) return '';
  if (s.startsWith('queue_')) s = s.slice(6);
  // Strip one or more trailing _YYYYMMDD[_HHMMSS] / _YYYYMMDDHHMMSS suffixes.
  for (let i = 0; i < 3; i += 1) {
    const next = s.replace(/_(\d{8})(_?\d{6})?$/, '');
    if (next === s) break;
    s = next;
  }
  return s || String(name);
}

/** Map a dataset/queue status code to a localized label. */
export function datasetStatusLabel(status: unknown, t: Translate): string {
  const key = String(status ?? '').trim().toLowerCase();
  switch (key) {
    case 'processing':
      return t('create:statusProcessing');
    case 'completed':
      return t('create:statusCompleted');
    case 'no_valid_chunks':
      return t('create:statusNoValidChunks');
    case 'failed':
      return t('create:statusFailed');
    case 'completed_with_failures':
      return t('create:statusCompletedWithFailures');
    case 'in_progress':
      return t('create:statusInProgress');
    case 'pending':
      return t('create:statusPending');
    default:
      return key || t('common:none');
  }
}
