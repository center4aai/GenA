import { FormEvent, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';
import { login } from '@/shared/api/dataset';
import { loginAuth, logoutAuth, resolveLoginRedirectPath, roleLabel, useAuth } from './authStore';
import { PageHeader } from '@/shared/ui/PageHeader';
import { formatUserError } from '@/shared/lib/errorMessages';

export function LoginPage() {
  const { t } = useTranslation('common');
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = resolveLoginRedirectPath((location.state as { from?: string } | null)?.from);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const data = await login(username, password);
      const token = data.access_token ?? data.token;
      if (!token) {
        setError(t('common:authNoToken'));
        return;
      }
      loginAuth(token, username, data.role ?? 'user');
      navigate(from, { replace: true });
    } catch (err) {
      setError(formatUserError(err, t, 'login'));
    } finally {
      setLoading(false);
    }
  }

  function handleLogout() {
    logoutAuth();
    setUsername('');
    setPassword('');
  }

  return (
    <div className="mx-auto max-w-md">
      <PageHeader title={t('common:authTitle')} subtitle={t('common:authSubtitle')} />

      {auth.token ? (
        <div className="card mt-6 space-y-4">
          <p className="text-green-700">
            {t('common:authSignedInAs', { username: auth.username })} ({roleLabel(auth.role, t)})
          </p>
          <button type="button" onClick={handleLogout} className="btn-secondary">
            {t('common:authLogout')}
          </button>
        </div>
      ) : (
        <form onSubmit={handleLogin} className="card mt-6 space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">{t('common:authUsername')}</label>
            <input
              className="input-field"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">{t('common:authPassword')}</label>
            <input
              type="password"
              className="input-field"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button type="submit" disabled={loading} className="btn-primary w-full">
            {loading ? t('common:authSigningIn') : t('common:authLogin')}
          </button>
        </form>
      )}
    </div>
  );
}
