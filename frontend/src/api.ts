export type Status =
  'idle' | 'submitting' | 'queued' | 'running' | 'completed' | 'failed';
export interface Session {
  login_id: string;
  csrf_token: string;
  expires_at: number;
  source_max_chars: number;
}
export interface ReviewSummary {
  review_id: string;
  language: string;
  status: Status;
  created_at: number;
  updated_at: number;
  error_code: string | null;
}
export interface Review extends ReviewSummary {
  source_code: string;
  review_result: string | null;
  error_message: string | null;
  model_id: string | null;
  model_revision: string | null;
}
export interface HistoryPage {
  items: ReviewSummary[];
  next_cursor: string | null;
}
export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
  csrf?: string,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...options,
      credentials: 'same-origin',
      cache: 'no-store',
      signal: options.signal ?? AbortSignal.timeout(15000),
      headers: {
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
        ...options.headers,
      },
    });
  } catch {
    throw new ApiError(
      'network_failure',
      'Cannot connect to the local service. Check that the runtime environment and local access channel are running.',
      0,
    );
  }
  if (response.status === 204) return undefined as T;
  let data;
  try {
    data = await response.json();
  } catch {
    throw new ApiError(
      'invalid_response',
      'The service returned an unreadable response. Please retry.',
      response.status,
    );
  }
  if (!response.ok)
    throw new ApiError(
      data.error?.code ?? 'service_unavailable',
      errorMessages[data.error?.code] ??
        data.error?.message ??
        'The service is temporarily unavailable. Please retry.',
      response.status,
    );
  return data;
}
export const isActive = (status: Status) =>
  ['submitting', 'queued', 'running'].includes(status);

const errorMessages: Record<string, string> = {
  model_loading:
    'The local model service is preparing and loading. Submit when it is ready.',
  inference_draining:
    'The previous inference is stopping. Please wait for service readiness.',
  inference_stuck:
    'Inference has not stopped. Check the backend logs before restarting the service.',
  startup_or_storage_failure:
    'Service startup or storage failed. Check the backend logs for details.',
  storage_unavailable:
    'Review storage is unavailable. Check the backend logs and instance storage.',
};
