import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react';
import { setToken, clearToken, getToken } from '@/shared/api/http';

const USERNAME_KEY = 'gena_username';
const ROLE_KEY = 'gena_role';

interface AuthState {
  token: string | null;
  username: string | null;
  role: string | null;
}

// --- Authentication temporarily hidden ---------------------------------
// The backend currently treats every request as a "public" expert (see
// dataset_api/auth_utils.get_current_user), so the UI runs as a synthetic
// authenticated expert: no login is required and all expert-only features
// stay enabled. A previously stored token/role is still honoured if present.
//
// To restore real authentication:
//   1. revert this block to the original (commented) version below,
//   2. re-enable the /login route + <RequireAuth> in app/router.tsx,
//   3. re-enable the sign-in/out UI in app/layout.tsx.
let state: AuthState = {
  token: getToken() ?? 'public',
  username: localStorage.getItem(USERNAME_KEY) ?? 'public',
  role: localStorage.getItem(ROLE_KEY) ?? 'expert',
};
// let state: AuthState = {
//   token: getToken(),
//   username: localStorage.getItem(USERNAME_KEY),
//   role: localStorage.getItem(ROLE_KEY),
// };

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

export function loginAuth(token: string, username: string, role = 'user') {
  setToken(token);
  localStorage.setItem(USERNAME_KEY, username);
  localStorage.setItem(ROLE_KEY, role);
  state = { token, username, role };
  emit();
}

export function logoutAuth() {
  clearToken();
  localStorage.removeItem(USERNAME_KEY);
  localStorage.removeItem(ROLE_KEY);
  state = { token: null, username: null, role: null };
  emit();
}

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
