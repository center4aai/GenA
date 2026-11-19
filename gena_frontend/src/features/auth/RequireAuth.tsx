import { useLayoutEffect } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { AUTH_ENABLED } from '@/shared/config/auth';
import { isAuthSessionComplete, persistAuthReturnTo, safeAuthReturnPath, useAuth } from './authStore';

export function RequireAuth() {
  const auth = useAuth();
  const location = useLocation();
  const returnTo = safeAuthReturnPath(`${location.pathname}${location.search}${location.hash}`);
  const needsAuth = AUTH_ENABLED && !isAuthSessionComplete(auth);

  useLayoutEffect(() => {
    if (needsAuth) persistAuthReturnTo(returnTo);
  }, [needsAuth, returnTo]);

  if (needsAuth) {
    return <Navigate to="/login" replace state={{ from: returnTo }} />;
  }

  return <Outlet />;
}
