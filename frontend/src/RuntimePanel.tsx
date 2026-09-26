import {
  canSubmit,
  environmentLabel,
  modeLabel,
  servicePresentation,
} from './runtime';
import type { RuntimeState } from './runtime';

export function RuntimePanel({ runtime }: { runtime: RuntimeState }) {
  const [title, detail] = servicePresentation(runtime);
  return (
    <section className="runtime-panel" aria-label="Instance runtime">
      <div className="runtime-facts">
        <span>{environmentLabel(runtime.info)}</span>
        <strong
          className={
            runtime.info?.inference_mode === 'simulated'
              ? 'simulated-label'
              : ''
          }
        >
          {modeLabel(runtime.info)}
        </strong>
      </div>
      <p className="runtime-model">
        Model: {runtime.info?.model_id ?? 'Unknown'}
      </p>
      <div role="status" aria-label="Service status" className="service-status">
        <span
          className={`status-dot ${canSubmit(runtime) ? 'completed' : runtime.connection === 'checking' ? 'queued' : 'failed'}`}
          aria-hidden="true"
        />
        <strong>{title}</strong>
        <span>{detail}</span>
      </div>
    </section>
  );
}
export function AboutInstance({ runtime }: { runtime: RuntimeState }) {
  const info = runtime.info;
  return (
    <details className="about-instance">
      <summary>About this instance</summary>
      <div>
        <p>
          {environmentLabel(info)}. {modeLabel(info)}.
        </p>
        <p>
          Model: {info?.model_id ?? 'Unknown'}. Revision:{' '}
          <code>{info?.model_revision ?? 'Unknown'}</code>. Source:{' '}
          {info?.model_source === 'backend_configuration'
            ? 'backend model configuration (pinned weight revision)'
            : info?.model_source === 'test_fixture'
              ? 'deterministic test fixture, not Qwen weights'
              : 'unknown'}
          .
        </p>
        <p>
          Accepted snippets are saved to the queue, processed one at a time, and
          stored with their results in your account history. Submission can
          still be rejected if the queue is full or account limits apply.
        </p>
        <p>
          Local accounts belong to this instance and isolate review history.
          They do not provide cloud accounts, cross-device sync, or shared
          accounts across instances.
        </p>
        <p>
          Data uses the running instance’s local storage. Retention depends on
          the deployment and storage lifecycle. Development harness accounts
          reset when the harness restarts.
        </p>
        <p>
          The built-in Qwen adapter uses local CPU inference; the simulated
          adapter returns a test fixture. Neither calls an external inference
          API or executes submitted source code. Initial environment or model
          preparation may download dependencies and weights.
        </p>
        <p>
          Environment comes from explicit backend deployment configuration,
          inference mode from the active model adapter, and service status from
          the existing readiness checks. These are service observations, not
          cluster diagnostics. Result metadata belongs to each saved review;
          older simulated records may contain the configured Qwen name and
          cannot establish real inference.
        </p>
      </div>
    </details>
  );
}
