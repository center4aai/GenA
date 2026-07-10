import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useTocSections } from '@/shared/lib/toc';
import { listDatasets } from '@/shared/api/dataset';
import { computeStatistics, statisticsQueryKey } from '@/features/statistics/statisticsData';
// Authentication temporarily hidden — see features/auth/authStore.tsx.
// import { useAuth, logoutAuth } from '@/features/auth/authStore';

/**
 * Warm the Statistics cache in the background as soon as the app opens, so the
 * page usually shows fresh data instantly when the user navigates to it. Uses
 * the exact query keys the page reads, so no extra fetching happens there while
 * the data is still fresh (staleTime).
 */
function useStatisticsPrefetch() {
  const qc = useQueryClient();
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const datasets = await qc.fetchQuery({
          queryKey: ['datasets'],
          queryFn: listDatasets,
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
  }, [qc]);
}

const NAV_ITEMS = [
  { to: '/', label: 'Home', end: true, icon: '⌂' },
  { to: '/data_preprocessing', label: 'Data Preprocessing', icon: '⚙' },
  { to: '/dataset_editor', label: 'Results & Editor', icon: '✎' },
  { to: '/queue_manager', label: 'Queue Manager', icon: '▤' },
  { to: '/statistics', label: 'Statistics', icon: '◫' },
  { to: '/dynamic_implementation', label: 'Dynamic Implementation', icon: '↻' },
  { to: '/docs', label: 'Documentation', icon: '?' },
];

export function Layout() {
  // const auth = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { pathname } = useLocation();
  const sections = useTocSections();
  useStatisticsPrefetch();

  const scrollToSection = (e: React.MouseEvent, id: string) => {
    e.preventDefault();
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      window.history.replaceState(null, '', `#${id}`);
    }
    setMobileOpen(false);
  };

  return (
    <div className="flex h-screen overflow-hidden">
      {mobileOpen && (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-slate-900/50 lg:hidden"
          aria-label="Close menu"
          onClick={() => setMobileOpen(false)}
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col bg-gena-sidebar text-stone-100 transition-transform lg:static lg:translate-x-0 ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex items-center gap-3 border-b border-stone-700/50 px-5 py-5">
          <img
            src="/logo.png"
            alt="GenA logo"
            className="h-11 w-11 shrink-0 rounded-xl bg-gena-surface object-contain p-0.5 shadow-sm ring-1 ring-stone-700/40"
          />
          <div className="min-w-0">
            <div className="truncate text-lg font-bold tracking-tight">GenA Framework</div>
            <div className="truncate text-xs text-stone-400">Generation & Sensitivity Assessment</div>
          </div>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto p-3">
          {NAV_ITEMS.map((item) => {
            const isActive = item.end ? pathname === item.to : pathname.startsWith(item.to);
            return (
              <div key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end}
                  onClick={() => setMobileOpen(false)}
                  className={`nav-link ${isActive ? 'nav-link-active' : 'nav-link-inactive'}`}
                >
                  <span className="w-4 text-center text-xs opacity-70">{item.icon}</span>
                  {item.label}
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
          })}
        </nav>

        {/* Authentication temporarily hidden — see features/auth/authStore.tsx.
        <div className="border-t border-stone-700/50 p-4 text-xs text-stone-400">
          {auth.username ? (
            <div className="space-y-2">
              <div>
                <div className="font-medium text-stone-200">{auth.username}</div>
                <div className="capitalize">{auth.role ?? 'user'}</div>
              </div>
              <div className="flex gap-3">
                <NavLink to="/login" className="text-gena-accent hover:underline" onClick={() => setMobileOpen(false)}>
                  Account
                </NavLink>
                <button type="button" className="text-stone-400 hover:text-white" onClick={() => logoutAuth()}>
                  Sign out
                </button>
              </div>
            </div>
          ) : (
            <NavLink to="/login" className="text-gena-accent hover:underline" onClick={() => setMobileOpen(false)}>
              Sign in
            </NavLink>
          )}
        </div>
        */}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-slate-200 bg-white/90 px-4 py-3 backdrop-blur lg:hidden">
          <button
            type="button"
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
            onClick={() => setMobileOpen(true)}
          >
            Menu
          </button>
          <span className="font-semibold text-slate-800">GenA 2.0</span>
        </header>

        <main className="flex-1 overflow-auto">
          <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
