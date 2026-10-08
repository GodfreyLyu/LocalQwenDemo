export const runtimeInfo = {
  deployment_environment: 'minikube',
  inference_mode: 'real',
  service_status: 'ready',
  accepting_submissions: true,
  model_id: 'Qwen/Qwen3-1.7B',
  model_revision: '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e',
  model_source: 'ollama_api',
  device: 'cpu',
};

export const session = {
  login_id: 'alice',
  csrf_token: 'csrf-token',
  expires_at: Date.now() / 1000 + 1800,
  source_max_chars: 12000,
};

export const review = {
  review_id: 'review-1',
  status: 'completed',
  language: 'python',
  source_code: 'x = 1',
  review_result: '## Findings\nA useful suggestion.',
  created_at: Date.now() / 1000,
  model_id: 'Qwen/Qwen3-1.7B',
  model_revision: '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e',
};

export const response = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

export function mockWorkspace(
  extra?: (
    path: string,
    options?: RequestInit,
  ) => Response | Promise<Response> | undefined,
) {
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    const custom = extra?.(path, options);
    if (custom) return Promise.resolve(custom);
    if (path === '/api/v1/runtime')
      return Promise.resolve(response(runtimeInfo));
    if (path.endsWith('/auth/me')) return Promise.resolve(response(session));
    if (path === '/api/v1/reviews')
      return Promise.resolve(response({ items: [], next_cursor: null }));
    return Promise.resolve(response(review));
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
