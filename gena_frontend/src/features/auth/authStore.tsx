import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react';
import { setToken, clearToken, getToken, registerUnauthorizedHandler } from '@/shared/api/http';
import { AUTH_ENABLED } from '@/shared/config/auth';

const USERNAME_KEY = 'gena_username';
const ROLE_KEY = 'gena_role';
const RETURN_TO_KEY = 'gena_auth_return_to';
const DEFAULT_RETURN_PATH = '/data_preprocessing';
const SYNTHETIC_TOKEN = 'public';
const SYNTHETIC_USERNAME = 'public';

interface AuthState {
  token: string | null;
  username: string | null;
  role: string | null;
}

function buildInitialState(): AuthState {
  if (!AUTH_ENABLED) {
    return {
      token: getToken() ?? 'public',
      username: localStorage.getItem(USERNAME_KEY) ?? 'public',
      role: localStorage.getItem(ROLE_KEY) ?? 'expert',
    };
  }
  const token = getToken();
  const username = localStorage.getItem(USERNAME_KEY);
  const role = localStorage.getItem(ROLE_KEY);
  if (token === SYNTHETIC_TOKEN || username === SYNTHETIC_USERNAME) {
    clearToken();
    localStorage.removeItem(USERNAME_KEY);
    localStorage.removeItem(ROLE_KEY);
    return { token: null, username: null, role: null };
  }
  if (!token && (username || role)) {
    localStorage.removeItem(USERNAME_KEY);
    localStorage.removeItem(ROLE_KEY);
    return { token: null, username: null, role: null };
  }
  if (token && (!username || !role)) {
    clearToken();
    localStorage.removeItem(USERNAME_KEY);
    localStorage.removeItem(ROLE_KEY);
    return { token: null, username: null, role: null };
  }
  return { token, username, role };
}

let state: AuthState = buildInitialState();

const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return state;
}

export function safeAuthReturnPath(
  path: string | undefined | null,
  fallback = DEFAULT_RETURN_PATH,
): string {
  if (!path || !path.startsWith('/') || path.startsWith('//')) return fallback;
  if (path === '/login' || path.startsWith('/login?') || path.startsWith('/login/')) return fallback;
  return path;
}

export function persistAuthReturnTo(path: string): void {
  const safe = safeAuthReturnPath(path, '');
  if (safe) sessionStorage.setItem(RETURN_TO_KEY, safe);
}

export function takeAuthReturnTo(fallback = DEFAULT_RETURN_PATH): string {
  const stored = sessionStorage.getItem(RETURN_TO_KEY);
  sessionStorage.removeItem(RETURN_TO_KEY);
  return safeAuthReturnPath(stored, fallback);
}

export function isAuthSessionComplete(auth: AuthState): boolean {
  return Boolean(
    auth.token &&
      auth.username &&
      auth.token !== SYNTHETIC_TOKEN &&
      auth.username !== SYNTHETIC_USERNAME,
  );
}

export function resolveLoginRedirectPath(stateFrom?: string | null): string {
  return safeAuthReturnPath(stateFrom, '') || takeAuthReturnTo();
}

export function loginAuth(token: string, username: string, role = 'user') {
  setToken(token);
  localStorage.setItem(USERNAME_KEY, username);
  localStorage.setItem(ROLE_KEY, role);
  sessionStorage.removeItem(RETURN_TO_KEY);
  state = { token, username, role };
  emit();
}

function invalidateAuthSession(options?: { preserveReturnTo?: boolean }): void {
  clearToken();
  localStorage.removeItem(USERNAME_KEY);
  localStorage.removeItem(ROLE_KEY);
  if (!options?.preserveReturnTo) {
    sessionStorage.removeItem(RETURN_TO_KEY);
  }
  state = AUTH_ENABLED
    ? { token: null, username: null, role: null }
    : buildInitialState();
  emit();
}

function handleUnauthorizedSession(): void {
  persistAuthReturnTo(
    `${window.location.pathname}${window.location.search}${window.location.hash}`,
  );
  invalidateAuthSession({ preserveReturnTo: true });
}

export function logoutAuth() {
  invalidateAuthSession();
}

registerUnauthorizedHandler(handleUnauthorizedSession);

export function useAuthStore() {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

const AuthContext = createContext<AuthState>(state);

export function AuthProvider({ children }: { children: ReactNode }) {
  const auth = useAuthStore();
  return <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}

export function isExpert(role: string | null): boolean {
  return role === 'expert' || role === 'admin';
}

/** Localized label for a backend role code. */
export function roleLabel(
  role: string | null | undefined,
  t: (key: string) => string,
): string {
  return isExpert(String(role ?? '')) ? t('common:roleExpert') : t('common:roleUser');
}
