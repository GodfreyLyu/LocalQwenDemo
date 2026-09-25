import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowRight,
  Check,
  Code2,
  FileCode2,
  History,
  LoaderCircle,
  LogOut,
  Plus,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { api, ApiError, isActive } from './api';
import type {
  HistoryPage,
  Review,
  ReviewSummary,
  Session,
  Status,
} from './api';
import { CodeEditor } from './CodeEditor';
import { Markdown } from './Markdown';
import {
  estimateModelTimeMs,
  formatEstimatedModelTime,
} from './modelTimeEstimate';

const example =
  'def average(values):\n    total = sum(values)\n    return total / len(values)\n';
const labels: Record<Status, string> = {
  idle: 'Ready when you are',
  submitting: 'Submitting your review…',
  queued: 'In the queue. Your code is saved.',
  running: 'Reviewing your code…',
  completed: 'Review complete',
  failed: 'Review could not be completed',
};
export default function App() {
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
    <Workspace session={session} onLogout={() => setSession(null)} />
  ) : (
    <Auth onLogin={setSession} />
  );
}

function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark">
        <Code2 size={23} />
      </span>
      <span>
        Local<span className="brand-light">QwenDemo</span>
      </span>
    </div>
  );
}

function Auth({ onLogin }: { onLogin: (session: Session) => void }) {
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
      <section className="auth-story">
        <Brand />
        <div>
          <span className="eyebrow">A FRESH PERSPECTIVE</span>
          <h1>
            Good code gets
            <br />a second look.
          </h1>
          <p>
            Find the subtle bugs. Understand the tradeoffs.
            <br />
            Move forward with a little more confidence.
          </p>
          <div className="code-card">
            <div className="code-card-top">
              <span />
              <span />
              <span />
              <small>one small improvement</small>
            </div>
            <pre>
              <span className="code-muted">{'def average(values):'}</span>
              {'\n'}
              <span className="code-add">{'    if not values:'}</span>
              {'\n'}
              <span className="code-add">{'        return 0'}</span>
              {'\n'}
              <span className="code-muted">
                {'    return sum(values) / len(values)'}
              </span>
            </pre>
            <div className="code-card-note">
              <Check size={16} /> An edge case, caught before it matters.
            </div>
          </div>
        </div>
        <div className="privacy-note">
          <ShieldCheck size={18} />
          <span>
            Reviewed by a local model on our backend.
            <br />
            Your source code is never executed.
          </span>
        </div>
      </section>
      <section className="auth-form-wrap">
        <form onSubmit={submit} className="auth-form">
          <span className="eyebrow">YOUR REVIEW WORKSPACE</span>
          <h2>{register ? 'Make room for better code.' : 'Welcome back.'}</h2>
          <p>
            {register
              ? 'Create an account to save your independent code reviews.'
              : 'Sign in to pick up where you left off.'}
          </p>
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
            {busy ? (
              <LoaderCircle className="spin" size={18} />
            ) : (
              <ArrowRight size={18} />
            )}
            {register ? 'Create account' : 'Sign in'}
          </button>
          <p className="auth-switch">
            {register ? 'Already have an account?' : 'New to LocalQwenDemo?'}{' '}
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
          <div className="auth-footnote">
            One submission. One focused review.
            <br />
            No conversations to manage.
          </div>
        </form>
      </section>
    </main>
  );
}

function Workspace({
  session,
  onLogout,
}: {
  session: Session;
  onLogout: () => void;
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
  const [pendingId, setPendingId] = useState<string | null>(null);
  const locked = useRef(false);
  const mounted = useRef(true);
  const requestKey = useRef<string | null>(null);
  const busy = isActive(status);
  const estimatedModelTime = formatEstimatedModelTime(
    estimateModelTimeMs(
      selected?.source_code.length ?? code.length,
      session.source_max_chars,
    ),
  );
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
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const review = await api<Review>(`/api/v1/reviews/${pendingId}`);
        if (canceled) return;
        setSelected(review);
        setCode(review.source_code);
        setLanguage(review.language);
        setStatus(review.status);
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
      clearTimeout(timer);
    };
  }, [pendingId, expired, failure, refreshHistory]);
  async function runReview() {
    if (locked.current || blocked) return;
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
    // Retry uncertain delivery with the same key and frozen input, never a second job.
    while (mounted.current) {
      try {
        const accepted = await api<{ review_id: string; status: Status }>(
          '/api/v1/reviews',
          { method: 'POST', body },
          session.csrf_token,
        );
        if (!mounted.current) return;
        setPendingId(accepted.review_id);
        setStatus(accepted.status);
        setMessage('');
        return;
      } catch (error) {
        if (!mounted.current) return;
        failure(error);
        if (
          error instanceof ApiError &&
          (error.status === 0 || error.status >= 500)
        ) {
          setMessage(`${error.message} Reconnecting safely…`);
          await new Promise((resolve) => setTimeout(resolve, 3000));
          continue;
        }
        setStatus('failed');
        locked.current = false;
        if (error instanceof ApiError && error.code === 'review_active')
          void refreshHistory();
        return;
      }
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
          <History size={14} /> REVIEW HISTORY <span>{history.length}</span>
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
            <div className="history-empty">
              <FileCode2 size={24} />
              <p>A clean slate.</p>
              <span>Your reviews will appear here.</span>
            </div>
          )}
          {history.map((item) => (
            <button
              key={item.review_id}
              disabled={blocked}
              className={`history-item ${selected?.review_id === item.review_id ? 'selected' : ''}`}
              onClick={() => void selectReview(item.review_id)}
            >
              <FileCode2 size={17} />
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
              <i
                className={`status-dot ${item.status}`}
                aria-label={item.status}
              />
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
          <div className="model-label">
            <span className="status-dot completed" /> BACKEND INFERENCE
          </div>
          <p>Qwen3-1.7B</p>
          <div className="account">
            <span className="avatar">{session.login_id[0].toUpperCase()}</span>
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
        <header className="topbar">
          <div>
            <span className="breadcrumb">WORKSPACE</span>
            <span className="breadcrumb-divider">/</span>
            <span>Code review</span>
          </div>
          <span className="demo-pill">CUSTOMER DEMO</span>
        </header>
        <div className="work-content">
          <div className="page-heading">
            <div>
              <span className="eyebrow">AN INDEPENDENT SECOND LOOK</span>
              <h1>Small details. Better code.</h1>
              <p>Paste a snippet. Get a focused review of what matters.</p>
            </div>
            <div className="private-badge">
              <ShieldCheck size={16} /> No code execution
            </div>
          </div>
          {message && (
            <div role="alert" className="notice error">
              {message}
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
                <span>
                  <Code2 size={18} /> Source code
                </span>
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
              {!code && (
                <button
                  className="example-button"
                  disabled={blocked}
                  onClick={() => {
                    setCode(example);
                    setLanguage('python');
                  }}
                >
                  Try a small example <ArrowRight size={13} />
                </button>
              )}
              <div className="editor-footer">
                <span
                  className={
                    code.length > session.source_max_chars ? 'over-limit' : ''
                  }
                >
                  {code.length.toLocaleString('en')} /{' '}
                  {session.source_max_chars.toLocaleString('en')} characters
                </span>
                <button
                  className="primary run-button"
                  onClick={() => void runReview()}
                  disabled={
                    blocked ||
                    !code.trim() ||
                    code.length > session.source_max_chars
                  }
                >
                  {busy ? (
                    <LoaderCircle size={16} className="spin" />
                  ) : (
                    <Sparkles size={16} />
                  )}
                  Run Review
                </button>
              </div>
            </section>
            <section className="panel result-panel" aria-label="Review result">
              <div className="panel-title">
                <span>
                  <Sparkles size={17} /> Review
                </span>
                <span className={`result-status ${status}`}>
                  {status === 'completed'
                    ? 'COMPLETED'
                    : busy
                      ? 'IN PROGRESS'
                      : 'OUTPUT'}
                </span>
              </div>
              <div className="result-body" aria-live="polite">
                {selected?.review_result ? (
                  <>
                    <Markdown content={selected.review_result} />
                    <div className="result-provenance">
                      {selected.model_id}
                      <br />
                      Revision {selected.model_revision}
                    </div>
                  </>
                ) : (
                  <div className="result-empty">
                    <div className={`result-icon ${busy ? 'active' : ''}`}>
                      {busy ? (
                        <LoaderCircle className="spin" size={27} />
                      ) : (
                        <FileCode2 size={27} />
                      )}
                    </div>
                    <h3>
                      {busy
                        ? labels[status]
                        : status === 'failed'
                          ? 'Let’s try that again.'
                          : 'A fresh pair of eyes.'}
                    </h3>
                    <p>
                      {busy
                        ? 'Your review is saved and processing. You can return to it from your history.'
                        : status === 'failed'
                          ? 'Check the message above, adjust your code if needed, and run a new review.'
                          : 'Your review will appear here, with findings, explanations, and practical suggestions.'}
                    </p>
                    {!busy && status !== 'failed' && (
                      <div className="review-tags">
                        <span>Correctness</span>
                        <span>Security</span>
                        <span>Clarity</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
              {busy && (
                <div className="model-time-estimate" aria-live="off">
                  <p>Estimated model time: about {estimatedModelTime}.</p>
                  <p>
                    Queue time, model loading, and system load can make the
                    total wait longer.
                  </p>
                </div>
              )}
              <div className="result-footer" role="status">
                <span className={`status-dot ${status}`} />
                {labels[status]}
              </div>
            </section>
          </div>
          <footer className="workspace-note">
            AI reviews can miss issues or make mistakes. Verify suggestions
            before applying them.<span>Each review starts fresh.</span>
          </footer>
        </div>
      </main>
    </div>
  );
}
