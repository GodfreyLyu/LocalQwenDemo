import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, isActive } from './api';
import type {
  HistoryPage,
  Review,
  ReviewSummary,
  Session,
  Status,
} from './api';
import { HistorySidebar } from './HistorySidebar';
import { ReviewResult } from './ReviewResult';
import { AboutInstance, RuntimePanel } from './RuntimePanel';
import { canSubmit } from './runtime';
import type { RuntimeState } from './runtime';
import { SourcePanel } from './SourcePanel';

const example =
  'def average(values):\n    total = sum(values)\n    return total / len(values)\n';

export function Workspace({
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
  const [pendingId, setPendingId] = useState<string | null>(null);

  // Keep the serialized input and idempotency key together across delivery retries.
  const pendingBody = useRef<string | null>(null);
  const requestKey = useRef<string | null>(null);
  // Refs take effect before React renders, so rapid clicks cannot start two actions.
  const locked = useRef(false);
  const mounted = useRef(true);
  const busy = isActive(status);
  const blocked = busy || working || historyLoading || expired;

  const showReview = useCallback((review: Review) => {
    setSelected(review);
    setCode(review.source_code);
    setLanguage(review.language);
    setStatus(review.status);
    setMessage(review.error_message ?? '');
  }, []);

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
          // Resume persisted work after a reload, before enabling another submission.
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
        showReview(review);
        if (isActive(review.status)) {
          timer = setTimeout(poll, 1500);
        } else {
          setPendingId(null);
          locked.current = false;
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
  }, [pendingId, expired, failure, refreshHistory, showReview]);

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
      // Readiness errors confirm rejection; transport and other server errors may
      // arrive after the job was saved, so they must keep the original request.
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
      showReview(review);
      requestKey.current = null;
    } catch (error) {
      failure(error);
    } finally {
      setWorking(false);
      locked.current = false;
    }
  }

  function resetSelection() {
    setSelected(null);
    setStatus('idle');
    requestKey.current = null;
  }

  function newReview() {
    if (locked.current || blocked) return;
    setCode('');
    setLanguage('auto');
    resetSelection();
    setMessage('');
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
      <HistorySidebar
        loginId={session.login_id}
        history={history}
        selectedId={selected?.review_id}
        cursor={cursor}
        historyLoading={historyLoading}
        historyError={historyError}
        blocked={blocked}
        reviewActive={busy}
        onNewReview={newReview}
        onRetryHistory={() => void refreshHistory()}
        onSelectReview={(id) => void selectReview(id)}
        onLoadOlder={(before) => void refreshHistory(before)}
        onLogout={() => void logout()}
      />
      <main className="main-area">
        <div className="work-content">
          <header className="workspace-header">
            <h1>Code review</h1>
            <RuntimePanel runtime={runtime} />
          </header>
          {message && (
            <div role="alert" className="notice">
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
            <SourcePanel
              code={code}
              language={language}
              sourceMaxChars={session.source_max_chars}
              blocked={blocked}
              runDisabled={
                blocked ||
                !canSubmit(runtime) ||
                !code.trim() ||
                code.length > session.source_max_chars
              }
              reviewActive={busy}
              onLanguageChange={(value) => {
                setLanguage(value);
                resetSelection();
              }}
              onCodeChange={(value) => {
                setCode(value);
                resetSelection();
              }}
              onLoadExample={() => {
                setCode(example);
                setLanguage('python');
              }}
              onRunReview={() => void runReview()}
            />
            <ReviewResult
              review={selected}
              status={status}
              deliveryUncertain={deliveryUncertain}
              runtime={runtime}
            />
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
