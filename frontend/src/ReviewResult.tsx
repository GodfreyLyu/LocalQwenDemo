import type { Review, Status } from './api';
import { Markdown } from './Markdown';
import type { RuntimeState } from './runtime';

const statusLabels: Record<Status, string> = {
  idle: 'No active review',
  submitting: 'Submitting your review…',
  queued: 'Queued · saved and waiting to run',
  running: 'Review in progress',
  completed: 'Review complete',
  failed: 'Review could not be completed',
};

function emptyResultMessage(status: Status, runtime: RuntimeState): string {
  switch (status) {
    case 'submitting':
      return 'Waiting for the service to confirm the submission. Your input is kept here.';
    case 'queued':
      return 'Your task is saved and waiting to run. You can revisit it from history.';
    case 'running':
      if (runtime.info?.inference_mode === 'simulated') {
        return 'Generating a deterministic test result. No real Qwen inference is running.';
      }
      if (runtime.info?.inference_mode === 'real') {
        return runtime.info.inference_backend === 'ollama'
          ? 'Reviewing with local Ollama. Time depends on code length and machine load.'
          : 'Reviewing on local CPU. Time depends on code length and machine load.';
      }
      return 'Waiting for the saved task to finish. Inference mode is unknown.';
    case 'failed':
      return 'Check the message above, adjust your code if needed, and run a new review.';
    default:
      return 'Submit code to see the review here.';
  }
}

interface ReviewResultProps {
  review: Review | null;
  status: Status;
  deliveryUncertain: boolean;
  runtime: RuntimeState;
}

export function ReviewResult({
  review,
  status,
  deliveryUncertain,
  runtime,
}: ReviewResultProps) {
  return (
    <section className="panel result-panel" aria-label="Review result">
      <div className="panel-title">
        <h2>Review result</h2>
      </div>
      <div className="result-body" aria-live="polite">
        {review?.review_result ? (
          <>
            <Markdown content={review.review_result} />
            <details className="result-provenance">
              <summary>
                {review.model_id === 'Simulated model'
                  ? 'Simulated result · no Qwen inference'
                  : 'Stored model metadata'}
              </summary>
              <div>
                {review.model_id ?? 'Model unknown'}
                <br />
                {review.model_revision?.startsWith('sha256:')
                  ? 'Digest'
                  : 'Revision'}{' '}
                {review.model_revision ?? 'unknown'}
              </div>
            </details>
          </>
        ) : (
          <div className="result-empty">
            <p>{emptyResultMessage(status, runtime)}</p>
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
        {deliveryUncertain ? 'Submission unconfirmed' : statusLabels[status]}
      </div>
    </section>
  );
}
