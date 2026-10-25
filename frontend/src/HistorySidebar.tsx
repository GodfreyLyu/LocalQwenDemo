import { LogOut, Plus } from 'lucide-react';
import type { ReviewSummary } from './api';
import { Brand } from './Brand';

interface HistorySidebarProps {
  loginId: string;
  history: ReviewSummary[];
  selectedId?: string;
  cursor: string | null;
  historyLoading: boolean;
  historyError: string;
  blocked: boolean;
  reviewActive: boolean;
  onNewReview: () => void;
  onRetryHistory: () => void;
  onSelectReview: (id: string) => void;
  onLoadOlder: (cursor: string) => void;
  onLogout: () => void;
}

export function HistorySidebar({
  loginId,
  history,
  selectedId,
  cursor,
  historyLoading,
  historyError,
  blocked,
  reviewActive,
  onNewReview,
  onRetryHistory,
  onSelectReview,
  onLoadOlder,
  onLogout,
}: HistorySidebarProps) {
  return (
    <aside className="sidebar">
      <Brand />
      <button className="new-review" disabled={blocked} onClick={onNewReview}>
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
              disabled={reviewActive}
              onClick={onRetryHistory}
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
            aria-current={selectedId === item.review_id ? 'true' : undefined}
            className={`history-item ${selectedId === item.review_id ? 'selected' : ''}`}
            onClick={() => onSelectReview(item.review_id)}
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
            onClick={() => onLoadOlder(cursor)}
          >
            Load older reviews
          </button>
        )}
      </div>
      <div className="sidebar-bottom">
        <div className="account" aria-label="Account">
          <div className="account-details">
            <span className="account-name" title={loginId}>
              {loginId}
            </span>
            <small
              className="local-account-note"
              title="Account for this instance only"
            >
              Local account
            </small>
          </div>
          <button
            className="icon-button"
            aria-label="Log out"
            title="Log out"
            onClick={onLogout}
            disabled={blocked}
          >
            <LogOut size={17} />
          </button>
        </div>
      </div>
    </aside>
  );
}
