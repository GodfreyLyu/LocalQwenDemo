import { useCallback, useEffect, useState } from 'react';
import { LoaderCircle } from 'lucide-react';
import { api, ApiError } from './api';
import type { Session } from './api';
import { Auth } from './Auth';
import { useRuntime } from './runtime';
import { Workspace } from './Workspace';

export default function App() {
  const runtime = useRuntime();
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [bootError, setBootError] = useState('');
  const restore = useCallback(() => {
    setChecking(true);
    setBootError('');
    api<Session>('/api/v1/auth/me')
      .then(setSession)
      .catch((error: ApiError) => {
        if (error.status !== 401) setBootError(error.message);
      })
      .finally(() => setChecking(false));
  }, []);
  useEffect(restore, [restore]);
  if (checking)
    return (
      <div className="boot">
        <LoaderCircle className="spin" /> Opening your workspace…
      </div>
    );
  if (bootError)
    return (
      <div className="boot">
        <p role="alert">{bootError}</p>
        <button onClick={restore}>Retry connection</button>
      </div>
    );
  return session ? (
    <Workspace
      session={session}
      onLogout={() => setSession(null)}
      runtime={runtime}
    />
  ) : (
    <Auth onLogin={setSession} runtime={runtime} />
  );
}
