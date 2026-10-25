import { useRef, useState } from 'react';
import { LoaderCircle } from 'lucide-react';
import { api } from './api';
import type { Session } from './api';
import { Brand } from './Brand';
import { AboutInstance, RuntimePanel } from './RuntimePanel';
import type { RuntimeState } from './runtime';

export function Auth({
  onLogin,
  runtime,
}: {
  onLogin: (session: Session) => void;
  runtime: RuntimeState;
}) {
  const [register, setRegister] = useState(false);
  const [loginId, setLoginId] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const locked = useRef(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError('');
    try {
      onLogin(
        await api<Session>(`/api/v1/auth/${register ? 'register' : 'login'}`, {
          method: 'POST',
          body: JSON.stringify({ login_id: loginId, password }),
        }),
      );
    } catch (error) {
      setError((error as Error).message);
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  return (
    <main className="auth-layout">
      <section className="auth-form-wrap">
        <Brand />
        <p className="auth-description">Local AI code review.</p>
        <RuntimePanel runtime={runtime} />
        <form onSubmit={submit} className="auth-form" aria-busy={busy}>
          <h1>{register ? 'Create account' : 'Sign in'}</h1>
          <label>
            Username
            <input
              autoComplete="username"
              value={loginId}
              onChange={(e) => setLoginId(e.target.value)}
              minLength={3}
              maxLength={100}
              placeholder="Enter your username"
              required
              disabled={busy}
            />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete={register ? 'new-password' : 'current-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={12}
              maxLength={128}
              placeholder="At least 12 characters"
              required
              disabled={busy}
            />
          </label>
          {error && (
            <div role="alert" className="notice">
              {error}
            </div>
          )}
          <button className="primary" disabled={busy}>
            {busy && (
              <LoaderCircle className="spin" size={16} aria-hidden="true" />
            )}
            {register ? 'Create account' : 'Sign in'}
          </button>
          <p className="auth-switch">
            {register ? 'Already have an account?' : 'New to this instance?'}{' '}
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => {
                setRegister(!register);
                setError('');
              }}
            >
              {register ? 'Sign in' : 'Create an account'}
            </button>
          </p>
        </form>
        <p className="auth-footnote">
          Local accounts belong to this instance and keep review histories
          separate.
        </p>
        <AboutInstance runtime={runtime} />
      </section>
    </main>
  );
}
