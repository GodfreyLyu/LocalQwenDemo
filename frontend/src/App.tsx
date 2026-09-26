import { useCallback, useEffect, useRef, useState } from 'react';
import { LoaderCircle, LogOut, Plus } from 'lucide-react';
import { api, ApiError, isActive } from './api';
import type {
  HistoryPage,
  Review,
  ReviewSummary,
  Session,
  Status,
} from './api';
import { canSubmit, useRuntime } from './runtime';
import { AboutInstance, RuntimePanel } from './RuntimePanel';
import type { RuntimeState } from './runtime';
import { CodeEditor } from './CodeEditor';
import { Markdown } from './Markdown';
const example =
  'def average(values):\n    total = sum(values)\n    return total / len(values)\n';
const labels: Record<Status, string> = {
  idle: 'No active review',
  submitting: 'Submitting your review…',
  queued: 'Queued · saved and waiting to run',
  running: 'Review in progress',
  completed: 'Review complete',
  failed: 'Review could not be completed',
};
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

function Brand() {
  return (
    <div className="brand">
      <span>
        Local<span className="brand-light">QwenDemo</span>
      </span>
    </div>
  );
}

function Auth({
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
            <div role="alert" className="notice error">
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

function Workspace({
  session,
  onLogout,
  runtime,
}: {
  session: Session;
  onLogout: () => void;
  runtime: RuntimeState;
}) {
  const [code, setCode] = useState('');
  const [language, setLanguage] = useState('auto');
  const [status, setStatus] = useState<Status>('idle');
  const [selected, setSelected] = useState<Review | null>(null);
  const [history, setHistory] = useState<ReviewSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(true);
  const [expired, setExpired] = useState(false);
  const [working, setWorking] = useState(false);
  const [deliveryUncertain, setDeliveryUncertain] = useState(false);
  const pendingBody = useRef<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const locked = useRef(false);
  const mounted = useRef(true);
  const requestKey = useRef<string | null>(null);
  const busy = isActive(status);
  const blocked = busy || working || historyLoading || expired;
  const failure = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.status === 401) {
      setExpired(true);
      setMessage(
        'Your session expired. Copy any unsaved code, then sign in again.',
      );
    } else setMessage((error as Error).message);
  }, []);
  const refreshHistory = useCallback(
    async (before?: string) => {
      setHistoryLoading(true);
      try {
        const page = await api<HistoryPage>(
          `/api/v1/reviews${before ? `?before=${before}` : ''}`,
        );
        setHistory((previous) =>
          before ? [...previous, ...page.items] : page.items,
        );
        setCursor(page.next_cursor);
        setHistoryError('');
        if (!before) {
          const active = page.items.find((item) => isActive(item.status));
          if (active) {
            locked.current = true;
            setStatus(active.status);
            setPendingId(active.review_id);
          }
        }
      } catch (error) {
        setHistoryError((error as Error).message);
        failure(error);
      } finally {
        setHistoryLoading(false);
      }
    },
    [failure],
  );
  useEffect(() => {
    mounted.current = true;
    void refreshHistory();
    return () => {
      mounted.current = false;
    };
  }, [refreshHistory]);
  useEffect(() => {
    if (!pendingId || expired) return;
    let canceled = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const review = await api<Review>(`/api/v1/reviews/${pendingId}`, {
          signal: AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(15000),
          ]),
        });
        if (canceled) return;
        setSelected(review);
        setCode(review.source_code);
        setLanguage(review.language);
        setStatus(review.status);
        setMessage(review.error_message ?? '');
        if (isActive(review.status)) {
          timer = setTimeout(poll, 1500);
        } else {
          setPendingId(null);
          locked.current = false;
          setMessage(review.error_message ?? '');
          requestKey.current = null;
          void refreshHistory();
        }
      } catch (error) {
        if (canceled) return;
        failure(error);
        if (error instanceof ApiError && [404, 422].includes(error.status)) {
          setPendingId(null);
          setStatus('failed');
          locked.current = false;
        } else timer = setTimeout(poll, 3000);
      }
    };
    void poll();
    return () => {
      canceled = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [pendingId, expired, failure, refreshHistory]);
  async function runReview() {
    if (locked.current || blocked || !canSubmit(runtime)) return;
    if (!code.trim()) {
      setMessage('Enter source code before running a review.');
      return;
    }
    if (code.length > session.source_max_chars) {
      setMessage(
        `Use at most ${session.source_max_chars.toLocaleString('en')} characters.`,
      );
      return;
    }
    locked.current = true;
    setStatus('submitting');
    setMessage('');
    setSelected(null);
    requestKey.current ??= crypto.randomUUID();
    const body = JSON.stringify({
      source_code: code,
      language,
      client_request_id: requestKey.current,
    });
    pendingBody.current = body;
    await deliverReview();
  }
  async function deliverReview() {
    if (!pendingBody.current) return;
    setDeliveryUncertain(false);
    setMessage('');
    try {
      const accepted = await api<{ review_id: string; status: Status }>(
        '/api/v1/reviews',
        { method: 'POST', body: pendingBody.current },
        session.csrf_token,
      );
      if (!mounted.current) return;
      pendingBody.current = null;
      setPendingId(accepted.review_id);
      setStatus(accepted.status);
    } catch (error) {
      if (!mounted.current) return;
      failure(error);
      const rejectedByReadiness =
        error instanceof ApiError &&
        [
          'model_loading',
          'inference_draining',
          'inference_stuck',
          'startup_or_storage_failure',
          'ready',
        ].includes(error.code);
      if (
        error instanceof ApiError &&
        !rejectedByReadiness &&
        (error.status === 0 || error.status >= 500)
      ) {
        // Keep the original input and request ID; retry only at the user's request.
        setDeliveryUncertain(true);
        setMessage(
          `${error.message} Delivery is unconfirmed. Retry with the same request ID to avoid creating a duplicate review.`,
        );
        return;
      }
      pendingBody.current = null;
      setStatus('failed');
      locked.current = false;
      if (error instanceof ApiError && error.code === 'review_active')
        void refreshHistory();
    }
  }
  async function selectReview(id: string) {
    if (locked.current || blocked) return;
    locked.current = true;
    setWorking(true);
    setMessage('');
    try {
      const review = await api<Review>(`/api/v1/reviews/${id}`);
      setSelected(review);
      setCode(review.source_code);
      setLanguage(review.language);
      setStatus(review.status);
      setMessage(review.error_message ?? '');
      requestKey.current = null;
    } catch (error) {
      failure(error);
    } finally {
      setWorking(false);
      locked.current = false;
    }
  }
  function newReview() {
    if (locked.current || blocked) return;
    setCode('');
    setLanguage('auto');
    setSelected(null);
    setStatus('idle');
    setMessage('');
    requestKey.current = null;
  }
  async function logout() {
    if (blocked || locked.current) return;
    setWorking(true);
    try {
      await api(
        '/api/v1/auth/logout',
        { method: 'POST', body: '{}' },
        session.csrf_token,
      );
      onLogout();
    } catch (error) {
      failure(error);
    } finally {
      setWorking(false);
    }
  }
  return (
    <div className="workspace">
      <aside className="sidebar">
        <Brand />
        <button className="new-review" disabled={blocked} onClick={newReview}>
          <Plus size={17} /> New review
        </button>
        <div className="sidebar-caption">
          History <span>{history.length}</span>
        </div>
        <div className="history-list">
          {historyLoading && <p className="history-empty">Loading history…</p>}
          {historyError && (
            <div className="history-error" role="alert">
              {historyError}
              <button
                className="text-button"
                disabled={busy}
                onClick={() => void refreshHistory()}
              >
                Retry history
              </button>
            </div>
          )}
          {!historyLoading && !history.length && !historyError && (
            <p className="history-empty">No reviews yet.</p>
          )}
          {history.map((item) => (
            <button
              key={item.review_id}
              disabled={blocked}
              aria-current={
                selected?.review_id === item.review_id ? 'true' : undefined
              }
              className={`history-item ${selected?.review_id === item.review_id ? 'selected' : ''}`}
              onClick={() => void selectReview(item.review_id)}
            >
              <span>
                <strong>
                  {item.language === 'auto' ? 'Code' : item.language} review
                </strong>
                <small>
                  {new Date(item.created_at * 1000).toLocaleString('en', {
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </small>
              </span>
              <span className="history-state">{item.status}</span>
            </button>
          ))}
          {cursor && (
            <button
              className="text-button load-more"
              disabled={blocked}
              onClick={() => void refreshHistory(cursor)}
            >
              Load older reviews
            </button>
          )}
        </div>
        <div className="sidebar-bottom">
          <p className="local-account-note">Local account · this instance</p>
          <div className="account">
            <span title={session.login_id}>{session.login_id}</span>
            <button
              className="icon-button"
              aria-label="Log out"
              title="Log out"
              onClick={() => void logout()}
              disabled={blocked}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <main className="main-area">
        <div className="work-content">
          <header className="workspace-header">
            <h1>Code review</h1>
            <RuntimePanel runtime={runtime} />
          </header>
          {message && (
            <div role="alert" className="notice error">
              <span>{message}</span>
              {deliveryUncertain && !expired && (
                <button
                  className="text-button"
                  onClick={() => void deliverReview()}
                >
                  Retry same submission
                </button>
              )}
              {expired && (
                <button className="text-button" onClick={onLogout}>
                  Return to sign in
                </button>
              )}
            </div>
          )}
          <div className="review-grid">
            <section className="panel source-panel">
              <div className="panel-title">
                <h2>Source code</h2>
                <select
                  aria-label="Programming language"
                  value={language}
                  disabled={blocked}
                  onChange={(e) => {
                    setLanguage(e.target.value);
                    setSelected(null);
                    setStatus('idle');
                    requestKey.current = null;
                  }}
                >
                  {[
                    ['auto', 'Auto detect'],
                    ['python', 'Python'],
                    ['javascript', 'JavaScript'],
                    ['typescript', 'TypeScript'],
                    ['json', 'JSON'],
                    ['html', 'HTML'],
                    ['css', 'CSS'],
                    ['plain', 'Other / Plain text'],
                  ].map(([value, label]) => (
                    <option value={value} key={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="editor-container">
                <CodeEditor
                  code={code}
                  language={language}
                  disabled={blocked}
                  onChange={(value) => {
                    setCode(value);
                    setSelected(null);
                    setStatus('idle');
                    requestKey.current = null;
                  }}
                />
              </div>
              <div className="editor-footer">
                <div className="editor-meta">
                  <span
                    className={
                      code.length > session.source_max_chars ? 'over-limit' : ''
                    }
                  >
                    {code.length.toLocaleString('en')} /{' '}
                    {session.source_max_chars.toLocaleString('en')} characters
                  </span>
                  {!code && (
                    <button
                      className="text-button example-button"
                      disabled={blocked}
                      onClick={() => {
                        setCode(example);
                        setLanguage('python');
                      }}
                    >
                      Load example
                    </button>
                  )}
                </div>
                <button
                  className="primary run-button"
                  onClick={() => void runReview()}
                  disabled={
                    blocked ||
                    !canSubmit(runtime) ||
                    !code.trim() ||
                    code.length > session.source_max_chars
                  }
                >
                  {busy && (
                    <LoaderCircle
                      size={16}
                      className="spin"
                      aria-hidden="true"
                    />
                  )}
                  Run review
                </button>
              </div>
            </section>
            <section className="panel result-panel" aria-label="Review result">
              <div className="panel-title">
                <h2>Review result</h2>
              </div>
              <div className="result-body" aria-live="polite">
                {selected?.review_result ? (
                  <>
                    <Markdown content={selected.review_result} />
                    <details className="result-provenance">
                      <summary>
                        {selected.model_id === 'Simulated model'
                          ? 'Simulated result · no Qwen inference'
                          : 'Stored model metadata'}
                      </summary>
                      <div>
                        {selected.model_id ?? 'Model unknown'}
                        <br />
                        Revision {selected.model_revision ?? 'unknown'}
                      </div>
                    </details>
                  </>
                ) : (
                  <div className="result-empty">
                    <p>
                      {busy
                        ? status === 'submitting'
                          ? 'Waiting for the service to confirm the submission. Your input is kept here.'
                          : status === 'queued'
                            ? 'Your task is saved and waiting to run. You can revisit it from history.'
                            : runtime.info?.inference_mode === 'simulated'
                              ? 'Generating a deterministic test result. No real Qwen inference is running.'
                              : runtime.info?.inference_mode === 'real'
                                ? 'Reviewing on local CPU. Time depends on code length and machine load.'
                                : 'Waiting for the saved task to finish. Inference mode is unknown.'
                        : status === 'failed'
                          ? 'Check the message above, adjust your code if needed, and run a new review.'
                          : 'Submit code to see the review here.'}
                    </p>
                  </div>
                )}
              </div>
              <div
                className="result-footer"
                role="status"
                aria-label="Review task status"
              >
                <span className={`status-dot ${status}`} />
                Task:{' '}
                {deliveryUncertain ? 'Submission unconfirmed' : labels[status]}
              </div>
            </section>
          </div>
          <AboutInstance runtime={runtime} />
          <footer className="workspace-note">
            Verify AI suggestions before applying them.
          </footer>
        </div>
      </main>
    </div>
  );
}
