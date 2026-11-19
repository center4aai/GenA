import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useTocSections } from '@/shared/lib/toc';
import { listDatasets } from '@/shared/api/dataset';
import { computeStatistics, statisticsQueryKey } from '@/features/statistics/statisticsData';
import { AUTH_ENABLED } from '@/shared/config/auth';
import { useAuth, logoutAuth, isAuthSessionComplete, roleLabel } from '@/features/auth/authStore';
import { LocaleSwitcher } from '@/shared/i18n/LocaleSwitcher';

/**
 * Warm the Statistics cache in the background as soon as the app opens, so the
 * page usually shows fresh data instantly when the user navigates to it. Uses
 * the exact query keys the page reads, so no extra fetching happens there while
 * the data is still fresh (staleTime).
 */
function useStatisticsPrefetch() {
  const auth = useAuth();
  const qc = useQueryClient();
  const { pathname } = useLocation();
  // Only warm up next to the page that uses it. Running on every route made
  // opening any page fetch every question of every dataset first — tens of MB
  // before the user could click anything.
  const nearStatistics = pathname === '/statistics' || pathname === '/queue_manager';
  useEffect(() => {
    if (!nearStatistics) return;
    if (AUTH_ENABLED && !isAuthSessionComplete(auth)) return;
    let cancelled = false;
    void (async () => {
      try {
        const datasets = await qc.fetchQuery({
          queryKey: ['datasets'],
          queryFn: () => listDatasets(),
          staleTime: 30_000,
        });
        if (cancelled || !datasets.length) return;
        await qc.prefetchQuery({
          queryKey: statisticsQueryKey(datasets),
          queryFn: () => computeStatistics(datasets),
          staleTime: 30_000,
        });
      } catch {
        // Best-effort warm-up; the page will fetch on its own if this fails.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [qc, auth.token, auth.username, nearStatistics]);
}

const PRIMARY_NAV = [
  { to: '/', labelKey: 'home', end: true, icon: '⌂' },
  { to: '/data_preprocessing', labelKey: 'create', icon: '⚙' },
  { to: '/dataset_editor', labelKey: 'results', icon: '✎' },
  { to: '/queue_manager', labelKey: 'progress', icon: '▤' },
] as const;

const USER_GUIDE_NAV = { to: '/docs#howto', labelKey: 'userGuide', icon: '?' } as const;

const ADVANCED_NAV = [
  { to: '/statistics', labelKey: 'analytics', icon: '◫' },
  { to: '/dynamic_implementation', labelKey: 'variants', icon: '↻' },
] as const;

export function Layout() {
  const auth = useAuth();
  const { t } = useTranslation(['nav', 'common']);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const { pathname } = useLocation();
  const sections = useTocSections();
  useStatisticsPrefetch();

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (ADVANCED_NAV.some((item) => pathname.startsWith(item.to))) {
      setAdvancedOpen(true);
    }
  }, [pathname]);

  const scrollToSection = (e: React.MouseEvent, id: string) => {
    e.preventDefault();
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      window.history.replaceState(null, '', `#${id}`);
    }
    setMobileOpen(false);
  };

  const renderNavItem = (item: { to: string; labelKey: string; end?: boolean; icon: string }) => {
    const [basePath, hash] = item.to.split('#');
    const isActive = item.end ? pathname === basePath : pathname.startsWith(basePath);
    return (
      <div key={item.to}>
        <NavLink
          to={item.to}
          end={item.end}
          onClick={(e) => {
            setMobileOpen(false);
            if (hash && pathname === basePath) scrollToSection(e, hash);
          }}
          className={`nav-link ${isActive ? 'nav-link-active' : 'nav-link-inactive'}`}
        >
          <span className="w-4 text-center text-xs opacity-70" aria-hidden>
            {item.icon}
          </span>
          {t(`nav:${item.labelKey}`)}
        </NavLink>
        {isActive && sections.length > 0 && (
          <div className="mb-1 ml-6 mt-1 space-y-0.5 border-l border-stone-700/50 pl-3">
            {sections.map((s) => (
              <a
                key={s.id}
                href={`#${s.id}`}
                onClick={(e) => scrollToSection(e, s.id)}
                className="block truncate rounded px-2 py-1 text-xs text-stone-400 transition-colors hover:bg-gena-sidebar-hover hover:text-white"
                title={s.label}
              >
                {s.label}
              </a>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="flex h-screen overflow-hidden">
      {mobileOpen && (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-slate-900/50 lg:hidden"
          aria-label={t('common:closeMenu')}
          onClick={() => setMobileOpen(false)}
        />
      )}

      <aside
        id="app-sidebar"
        className={`fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col bg-gena-sidebar text-stone-100 transition-transform lg:static lg:translate-x-0 ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex items-center gap-3 border-b border-stone-700/50 px-5 py-5">
          <img
            src="/logo.png"
            alt={t('common:logoAlt')}
            className="h-11 w-11 shrink-0 rounded-xl bg-gena-surface object-contain p-0.5 shadow-sm ring-1 ring-stone-700/40"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = 'none';
            }}
          />
            <div className="min-w-0">
              <div className="truncate text-lg font-bold tracking-tight">GenA</div>
              <div className="text-xs leading-snug text-stone-400" title={t('common:brandSubtitle')}>
                {t('common:brandSubtitle')}
              </div>
            </div>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto p-3" aria-label={t('common:mainNav')}>
          {PRIMARY_NAV.map(renderNavItem)}
          {renderNavItem(USER_GUIDE_NAV)}

          <div className="pt-2">
            <button
              type="button"
              className="nav-link nav-link-inactive w-full justify-between"
              aria-expanded={advancedOpen}
              onClick={() => setAdvancedOpen((o) => !o)}
            >
              <span className="flex items-center gap-2">
                <span className="w-4 text-center text-xs opacity-70" aria-hidden>
                  ▸
                </span>
                {t('nav:advancedSection')}
              </span>
              <span className="text-xs opacity-60">{advancedOpen ? '▾' : '▸'}</span>
            </button>
            {advancedOpen && (
              <div className="mt-1 space-y-1 pl-1">{ADVANCED_NAV.map(renderNavItem)}</div>
            )}
          </div>
        </nav>

        <div className="space-y-3 border-t border-stone-700/50 p-4">
          <LocaleSwitcher />
          {AUTH_ENABLED && (
            <div className="text-xs text-stone-400">
              {isAuthSessionComplete(auth) ? (
                <div className="space-y-2">
                  <div>
                    <div className="font-medium text-stone-200">{auth.username}</div>
                    <div className="capitalize">{roleLabel(auth.role, t)}</div>
                  </div>
                  <div className="flex gap-3">
                    <NavLink
                      to="/login"
                      className="text-gena-accent hover:underline"
                      onClick={() => setMobileOpen(false)}
                    >
                      {t('common:account')}
                    </NavLink>
                    <button
                      type="button"
                      className="text-stone-400 hover:text-white"
                      onClick={() => logoutAuth()}
                    >
                      {t('common:signOut')}
                    </button>
                  </div>
                </div>
              ) : (
                <NavLink
                  to="/login"
                  className="text-gena-accent hover:underline"
                  onClick={() => setMobileOpen(false)}
                >
                  {t('common:signIn')}
                </NavLink>
              )}
            </div>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur lg:hidden">
          <button
            type="button"
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            aria-expanded={mobileOpen}
            aria-controls="app-sidebar"
            aria-label={mobileOpen ? t('common:closeMenu') : t('common:openMenu')}
            onClick={() => setMobileOpen((open) => !open)}
          >
            {t('common:menu')}
          </button>
          <span className="flex-1 font-semibold text-slate-800">GenA</span>
          <LocaleSwitcher className="border-slate-300 [&_button]:text-slate-700 [&_button.bg-gena-accent]:bg-gena-primary [&_button.bg-gena-accent]:text-white" />
        </header>

        <main className="flex-1 overflow-auto" aria-label={t('common:mainContent')}>
          <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
