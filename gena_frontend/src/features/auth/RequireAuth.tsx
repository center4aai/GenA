import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from './authStore';

export function RequireAuth() {
  const auth = useAuth();
  const location = useLocation();

  if (!auth.token) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}
