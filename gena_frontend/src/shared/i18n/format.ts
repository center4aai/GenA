import { parseServerDate } from '@/shared/lib/dateTime';
import type { AppLocale } from './config';

/** BCP-47 tags for Intl formatters — must cover every AppLocale. */
export const LOCALE_TO_INTL: Record<AppLocale, string> = {
  en: 'en-US',
  ru: 'ru-RU',
};

function intlTag(locale: AppLocale): string {
  return LOCALE_TO_INTL[locale];
}

export function formatDisplayDate(value: unknown, locale: AppLocale): string {
  if (value == null || value === '') return '';
  const d = parseServerDate(value);
  if (!d) {
    const s = String(value);
    return s.slice(0, 19).replace('T', ' ');
  }
  return new Intl.DateTimeFormat(intlTag(locale), {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(d);
}

export function formatDisplayDateTime(value: unknown, locale: AppLocale): string {
  if (value == null || value === '') return '';
  const d = parseServerDate(value);
  if (!d) {
    const s = String(value);
    return s.slice(0, 19).replace('T', ' ');
  }
  return new Intl.DateTimeFormat(intlTag(locale), {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d);
}

export function formatPercent(value: number, locale: AppLocale, digits = 0): string {
  return new Intl.NumberFormat(intlTag(locale), {
    style: 'percent',
    maximumFractionDigits: digits,
  }).format(value / 100);
}

/** Suggest a human dataset name from a filename + today. */
export function suggestDatasetName(
  fileName: string,
  locale: AppLocale,
  documentFallback = 'Document',
): string {
  const base = fileName
    .replace(/\.[^.]+$/, '')
    .replace(/[_]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    || documentFallback;
  const date = formatDisplayDate(new Date(), locale);
  return `${base} — ${date}`;
}
