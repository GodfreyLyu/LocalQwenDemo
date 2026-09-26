import { useEffect, useState } from 'react';
import { api, ApiError } from './api';

export interface RuntimeInfo {
  deployment_environment: 'minikube' | 'development' | 'unknown';
  inference_mode: 'real' | 'simulated' | 'unknown';
  service_status: string;
  accepting_submissions: boolean;
  model_id: string | null;
  model_revision: string | null;
  model_source: 'backend_configuration' | 'test_fixture' | 'unknown';
  device: 'cpu' | null;
}
export interface RuntimeState {
  info: RuntimeInfo | null;
  connection: 'checking' | 'connected' | 'disconnected' | 'unknown';
}
export const RUNTIME_POLL_MS = 5000;

function validInfo(value: RuntimeInfo): boolean {
  return (
    !!value &&
    ['minikube', 'development', 'unknown'].includes(
      value.deployment_environment,
    ) &&
    ['real', 'simulated', 'unknown'].includes(value.inference_mode) &&
    typeof value.service_status === 'string' &&
    typeof value.accepting_submissions === 'boolean' &&
    (value.model_id === null || typeof value.model_id === 'string') &&
    (value.model_revision === null ||
      typeof value.model_revision === 'string') &&
    ['backend_configuration', 'test_fixture', 'unknown'].includes(
      value.model_source,
    ) &&
    (value.device === null || value.device === 'cpu')
  );
}
export function useRuntime(): RuntimeState {
  const [state, setState] = useState<RuntimeState>({
    info: null,
    connection: 'checking',
  });
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const info = await api<RuntimeInfo>('/api/v1/runtime', {
          signal: AbortSignal.any([
            controller.signal,
            AbortSignal.timeout(8000),
          ]),
        });
        if (!validInfo(info))
          throw new ApiError(
            'invalid_response',
            'Runtime status is unknown.',
            200,
          );
        if (!controller.signal.aborted)
          setState({ info, connection: 'connected' });
      } catch (error) {
        if (!controller.signal.aborted)
          setState({
            info: null,
            connection:
              error instanceof ApiError && error.status === 0
                ? 'disconnected'
                : 'unknown',
          });
      } finally {
        if (!controller.signal.aborted)
          timer = setTimeout(poll, RUNTIME_POLL_MS);
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, []);
  return state;
}
export function environmentLabel(info: RuntimeInfo | null) {
  return info?.deployment_environment === 'minikube'
    ? 'Local minikube'
    : info?.deployment_environment === 'development'
      ? 'Local development'
      : 'Environment unknown';
}
export function modeLabel(info: RuntimeInfo | null) {
  return info?.inference_mode === 'simulated'
    ? 'Simulated model (no Qwen inference)'
    : info?.inference_mode === 'real'
      ? 'Real model'
      : 'Inference mode unknown';
}
export function canSubmit(runtime: RuntimeState) {
  return (
    runtime.connection === 'connected' &&
    runtime.info?.service_status === 'ready' &&
    runtime.info.accepting_submissions === true
  );
}
export function servicePresentation(runtime: RuntimeState): [string, string] {
  if (runtime.connection === 'checking')
    return [
      'Checking service',
      'Checking whether this instance can accept submissions.',
    ];
  if (runtime.connection === 'disconnected')
    return [
      'Connection interrupted',
      'Cannot connect to the local service. Check that the runtime environment and local access channel are running.',
    ];
  if (runtime.connection === 'unknown' || !runtime.info)
    return [
      'Status unknown',
      'Runtime information is unavailable. Submissions are paused until status can be confirmed.',
    ];
  if (canSubmit(runtime))
    return [
      'Ready',
      'The service can accept submissions. Queue capacity and account limits are checked when you submit.',
    ];
  switch (runtime.info.service_status) {
    case 'model_loading':
      return [
        'Model loading',
        runtime.info.inference_mode === 'simulated'
          ? 'The simulated model service is preparing. Submit when it is ready.'
          : 'The local model service is preparing and loading. Submit when it is ready.',
      ];
    case 'inference_draining':
      return [
        'Temporarily unavailable',
        'The previous inference is stopping. Submissions will resume when the service is ready.',
      ];
    case 'inference_stuck':
      return [
        'Temporarily unavailable',
        'Inference has not stopped. Check the backend logs before restarting the service.',
      ];
    case 'storage_unavailable':
      return [
        'Storage unavailable',
        'The service cannot access review storage. Check the backend logs and instance storage.',
      ];
    case 'startup_or_storage_failure':
      return [
        'Service unavailable',
        'Service startup or storage failed. Check the backend logs for details.',
      ];
    case 'shutting_down':
      return [
        'Service stopping',
        'This instance is shutting down and cannot accept submissions.',
      ];
    default:
      return [
        'Status unknown',
        'The service has not confirmed readiness. Submissions are paused.',
      ];
  }
}
