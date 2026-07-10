import { FormEvent, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { login } from '@/shared/api/dataset';
import { loginAuth, logoutAuth, useAuth } from './authStore';
import { PageHeader } from '@/shared/ui/PageHeader';

export function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? '/data_preprocessing';
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
        setError('No token in response');
        return;
      }
      loginAuth(token, username, data.role ?? 'user');
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
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
      <PageHeader title="Sign in" subtitle="Access data pages, queues, and statistics." />

      {auth.token ? (
        <div className="card mt-6 space-y-4">
          <p className="text-green-700">Signed in as <strong>{auth.username}</strong> ({auth.role})</p>
          <button type="button" onClick={handleLogout} className="btn-secondary">
            Logout
          </button>
        </div>
      ) : (
        <form onSubmit={handleLogin} className="card mt-6 space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">Username</label>
            <input
              className="input-field"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Password</label>
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
            {loading ? 'Signing in…' : 'Login'}
          </button>
        </form>
      )}
    </div>
  );
}
