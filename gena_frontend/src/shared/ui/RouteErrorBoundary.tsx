import { useTranslation } from 'react-i18next';
import { useRouteError } from 'react-router-dom';

/**
 * Route-level crash screen. Without it, a render throw in a page — or a failed
 * lazy-chunk load right after a redeploy — left the user on a blank white
 * screen with no way out. A full reload also fetches the freshly deployed
 * chunks, which is exactly the fix for the post-deploy case.
 */
export function RouteErrorBoundary() {
  const { t } = useTranslation('errors');
  const error = useRouteError();
  const detail =
    error instanceof Error ? error.message : typeof error === 'string' ? error : '';

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-8 text-center">
      <h1 className="text-xl font-semibold text-slate-800">{t('routeCrashed')}</h1>
      {detail && (
        <p className="max-w-xl break-words font-mono text-xs text-slate-400">{detail}</p>
      )}
      <button type="button" className="btn-primary" onClick={() => window.location.reload()}>
        {t('reload')}
      </button>
    </div>
  );
}
