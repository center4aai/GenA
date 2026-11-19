import type { TFunction } from 'i18next';
import { HttpError } from '@/shared/api/http';

type ErrorContext = 'login' | 'save' | 'load' | 'chunker' | 'generic';

/** Map API / network errors to teacher-facing messages (via i18n `errors` ns). */
export function formatUserError(
  err: unknown,
  t: TFunction,
  context: ErrorContext = 'generic',
): string {
  if (err instanceof HttpError) {
    if (err.status === 401) {
      return context === 'login' ? t('errors:badCredentials') : t('errors:sessionExpired');
    }
    if (err.status === 403) return t('errors:forbidden');
    if (err.status === 502 || err.status === 503) return t('errors:unavailable');
    if (err.status === 413) return t('create:fileTooLarge');
    if (err.status === 422) return t('create:scannedPdfHint');

    const detail = (err.message || '').toLowerCase();
    if (detail.includes('model') && (detail.includes('unavail') || detail.includes('not respond'))) {
      return t('errors:modelUnavailable');
    }
    if (context === 'chunker' || detail.includes('chunker')) {
      return t('errors:chunkerFailed');
    }
    if (context === 'save') return t('errors:saveFailed', { detail: err.message });
  }

  const msg = err instanceof Error ? err.message : '';
  if (/failed to fetch|networkerror|load failed/i.test(msg)) {
    return t('errors:network');
  }
  if (context === 'chunker') return t('errors:chunkerFailed');
  if (context === 'load') return t('errors:loadFailed');
  if (msg) return msg;
  return t('errors:generic');
}
