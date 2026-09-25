import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';
import {
  BASE_ESTIMATE_MS,
  estimateModelTimeMs,
  formatEstimatedModelTime,
  MAX_ESTIMATE_MS,
} from './modelTimeEstimate';
import { Markdown } from './Markdown';
import { api, ApiError } from './api';

vi.mock('./CodeEditor', () => ({
  CodeEditor: ({
    code,
    disabled,
    onChange,
  }: {
    code: string;
    disabled: boolean;
    onChange: (value: string) => void;
  }) => (
    <textarea
      aria-label="Source code"
      value={code}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));
const session = {
  login_id: 'alice',
  csrf_token: 'csrf-token',
  expires_at: Date.now() / 1000 + 1800,
  source_max_chars: 12000,
};
const review = {
  review_id: 'review-1',
  status: 'completed',
  language: 'python',
  source_code: 'x = 1',
  review_result: '## Findings\nA useful suggestion.',
  created_at: Date.now() / 1000,
  model_id: 'Qwen/Qwen3-1.7B',
  model_revision: '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e',
};
const response = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
function mockWorkspace(
  extra?: (
    path: string,
    options?: RequestInit,
  ) => Response | Promise<Response> | undefined,
) {
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    const custom = extra?.(path, options);
    if (custom) return Promise.resolve(custom);
    if (path.endsWith('/auth/me')) return Promise.resolve(response(session));
    if (path === '/api/v1/reviews')
      return Promise.resolve(response({ items: [], next_cursor: null }));
    return Promise.resolve(response(review));
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}

it('uses a deterministic bounded input-size estimate with half-minute display precision', () => {
  expect(estimateModelTimeMs(0, session.source_max_chars)).toBe(
    BASE_ESTIMATE_MS,
  );
  expect(
    estimateModelTimeMs(1, session.source_max_chars),
  ).toBeGreaterThanOrEqual(BASE_ESTIMATE_MS);
  const medium = estimateModelTimeMs(6000, session.source_max_chars);
  const maximum = estimateModelTimeMs(12000, session.source_max_chars);
  expect(medium).toBeGreaterThan(BASE_ESTIMATE_MS);
  expect(maximum).toBe(MAX_ESTIMATE_MS);
  expect(estimateModelTimeMs(24000, session.source_max_chars)).toBe(
    MAX_ESTIMATE_MS,
  );
  expect(estimateModelTimeMs(500, 0)).toBe(BASE_ESTIMATE_MS);
  expect(estimateModelTimeMs(500, Number.NaN)).toBe(BASE_ESTIMATE_MS);
  expect(formatEstimatedModelTime(BASE_ESTIMATE_MS)).toBe('3.5 minutes');
  expect(formatEstimatedModelTime(MAX_ESTIMATE_MS)).toBe('5 minutes');
});

it('shows the current Qwen3 model label while idle', async () => {
  mockWorkspace();
  render(<App />);

  expect(await screen.findByText('Qwen3-1.7B')).toBeInTheDocument();
  expect(screen.queryByText(/Estimated model time:/)).not.toBeInTheDocument();
});

it('uses English dates and numbers even with non-English browser defaults', async () => {
  const formatDate = Date.prototype.toLocaleString;
  const formatNumber = Number.prototype.toLocaleString;
  // Simulate Japanese dates and German numbers when no explicit locale is supplied.
  vi.spyOn(Date.prototype, 'toLocaleString').mockImplementation(function (
    this: Date,
    locales,
    options,
  ) {
    return formatDate.call(
      this,
      !locales || (Array.isArray(locales) && locales.length === 0)
        ? 'ja-JP'
        : locales,
      options,
    );
  });
  vi.spyOn(Number.prototype, 'toLocaleString').mockImplementation(function (
    this: number,
    locales,
    options,
  ) {
    return formatNumber.call(this, locales ?? 'de-DE', options);
  });
  mockWorkspace((path) =>
    path === '/api/v1/reviews'
      ? response({ items: [review], next_cursor: null })
      : undefined,
  );
  render(<App />);

  const expectedDate = formatDate.call(
    new Date(review.created_at * 1000),
    'en',
    {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    },
  );
  expect(await screen.findByText(expectedDate)).toBeInTheDocument();
  expect(screen.getByText('0 / 12,000 characters')).toBeInTheDocument();
});

it('shows natural username copy on the login and registration forms', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve(response({ error: { message: 'Sign in' } }, 401)),
    ),
  );
  render(<App />);

  const username = await screen.findByLabelText('Username');
  expect(username).toHaveAttribute('placeholder', 'Enter your username');
  expect(screen.queryByText('login.identifier')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Create an account' }));
  expect(screen.getByLabelText('Username')).toHaveAttribute(
    'placeholder',
    'Enter your username',
  );
  expect(screen.queryByText('login.identifier')).not.toBeInTheDocument();
});

it('registers and opens the authenticated workspace', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      Promise.resolve(
        path.endsWith('/auth/me')
          ? response({ error: { message: 'Sign in' } }, 401)
          : path.endsWith('/auth/register')
            ? response(session, 201)
            : response({ items: [], next_cursor: null }),
      ),
    ),
  );
  render(<App />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Create an account' }),
  );
  fireEvent.change(screen.getByLabelText('Username'), {
    target: { value: 'alice' },
  });
  fireEvent.change(screen.getByLabelText('Password'), {
    target: { value: 'a-valid-password' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
  expect(
    await screen.findByText('Small details. Better code.'),
  ).toBeInTheDocument();
});

it('locks conflicting controls and prevents duplicate submissions until completion', async () => {
  let finish: (value: Response) => void = () => {};
  const fetcher = mockWorkspace((path, options) => {
    if (path === '/api/v1/reviews' && options?.method === 'POST')
      return new Promise((resolve) => {
        finish = resolve;
      });
  });
  render(<App />);
  const editor = await screen.findByLabelText('Source code');
  await waitFor(() => expect(editor).not.toBeDisabled());
  fireEvent.change(editor, { target: { value: 'x = 1' } });
  const run = screen.getByRole('button', { name: 'Run Review' });
  fireEvent.click(run);
  fireEvent.click(run);
  expect(
    screen.getByText('Estimated model time: about 3.5 minutes.'),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      'Queue time, model loading, and system load can make the total wait longer.',
    ),
  ).toBeInTheDocument();
  expect(editor).toBeDisabled();
  expect(screen.getByLabelText('Programming language')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'New review' })).toBeDisabled();
  expect(
    fetcher.mock.calls.filter(([, options]) => options?.method === 'POST'),
  ).toHaveLength(1);
  finish(response({ review_id: 'review-1', status: 'queued' }, 202));
  expect(await screen.findByText('A useful suggestion.')).toBeInTheDocument();
  expect(document.querySelector('.result-provenance')).toHaveTextContent(
    'Qwen/Qwen3-1.7BRevision 70d244cc86ccca08cf5af4e1e306ecf908b1ad5e',
  );
  await waitFor(() => expect(editor).not.toBeDisabled());
  expect(screen.getByRole('status')).toHaveTextContent('Review complete');
  expect(screen.queryByText(/Estimated model time:/)).not.toBeInTheDocument();
  expect(
    fetcher.mock.calls.filter(([path]) => path === '/api/v1/reviews/review-1'),
  ).toHaveLength(1);
});

it('rejects whitespace and input beyond the configured character limit', async () => {
  mockWorkspace();
  render(<App />);
  const editor = await screen.findByLabelText('Source code');
  await waitFor(() => expect(editor).not.toBeDisabled());
  fireEvent.change(editor, { target: { value: ' \n\t' } });
  expect(screen.getByRole('button', { name: 'Run Review' })).toBeDisabled();
  fireEvent.change(editor, { target: { value: 'x'.repeat(12001) } });
  expect(screen.getByRole('button', { name: 'Run Review' })).toBeDisabled();
});

it('shows queue-full errors and unlocks the workspace', async () => {
  mockWorkspace((path, options) =>
    path === '/api/v1/reviews' && options?.method === 'POST'
      ? response(
          {
            error: {
              code: 'queue_full',
              message: 'The review queue is full. Try again later.',
            },
          },
          429,
        )
      : undefined,
  );
  render(<App />);
  const editor = await screen.findByLabelText('Source code');
  await waitFor(() => expect(editor).not.toBeDisabled());
  fireEvent.change(editor, { target: { value: 'x=1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run Review' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('queue is full');
  expect(editor).not.toBeDisabled();
});

it('shows an invalid model response as a failed review and unlocks the workspace', async () => {
  mockWorkspace((path, options) => {
    if (path === '/api/v1/reviews' && options?.method === 'POST')
      return response({ review_id: 'review-1', status: 'queued' }, 202);
    if (path === '/api/v1/reviews/review-1')
      return response({
        ...review,
        status: 'failed',
        review_result: null,
        error_code: 'invalid_model_response',
        error_message:
          'The model returned an unusable review. Try a smaller, self-contained snippet.',
      });
  });
  render(<App />);
  const editor = await screen.findByLabelText('Source code');
  await waitFor(() => expect(editor).not.toBeDisabled());
  fireEvent.change(editor, { target: { value: 'def average(values): pass' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run Review' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'model returned an unusable review',
  );
  expect(screen.getByRole('status')).toHaveTextContent(
    'Review could not be completed',
  );
  expect(screen.queryByText(/Estimated model time:/)).not.toBeInTheDocument();
  expect(editor).not.toBeDisabled();
});

it('restores the source-sized estimate for a persisted running review', async () => {
  mockWorkspace((path) => {
    if (path === '/api/v1/reviews')
      return response({
        items: [{ ...review, status: 'running' }],
        next_cursor: null,
      });
    if (path === '/api/v1/reviews/review-1')
      return response({
        ...review,
        status: 'running',
        source_code: 'x'.repeat(6000),
        review_result: null,
        model_id: null,
        model_revision: null,
      });
  });
  render(<App />);

  expect(
    await screen.findByText('Estimated model time: about 4 minutes.'),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('Source code')).toHaveValue('x'.repeat(6000));
});

it('resumes an active review from persisted history', async () => {
  let historyCount = 0;
  mockWorkspace((path) =>
    path === '/api/v1/reviews'
      ? response({
          items:
            historyCount++ === 0
              ? [{ ...review, status: 'running' }]
              : [review],
          next_cursor: null,
        })
      : undefined,
  );
  render(<App />);
  expect(await screen.findByText('A useful suggestion.')).toBeInTheDocument();
  expect(screen.getByLabelText('Source code')).toHaveValue('x = 1');
});

it('preserves unsaved source on authentication expiry', async () => {
  mockWorkspace((path, options) =>
    path === '/api/v1/reviews' && options?.method === 'POST'
      ? response(
          { error: { code: 'session_expired', message: 'Expired' } },
          401,
        )
      : undefined,
  );
  render(<App />);
  const editor = await screen.findByLabelText('Source code');
  await waitFor(() => expect(editor).not.toBeDisabled());
  fireEvent.change(editor, { target: { value: 'unsaved code' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run Review' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Copy any unsaved code',
  );
  expect(editor).toHaveValue('unsaved code');
});

it('reports history failures and provides a retry action', async () => {
  mockWorkspace((path) =>
    path === '/api/v1/reviews'
      ? response(
          {
            error: {
              code: 'history_unavailable',
              message: 'History could not be loaded.',
            },
          },
          503,
        )
      : undefined,
  );
  render(<App />);
  expect(
    await screen.findByRole('button', { name: 'Retry history' }),
  ).toBeInTheDocument();
});

it('removes raw HTML, unsafe links, and remotely loaded images from model output', () => {
  const { container } = render(
    <Markdown
      content={
        '# Review\n<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n\n[bad](javascript:alert%281%29)\n![tracking](https://tracker.example/pixel)\n**Safe content**'
      }
    />,
  );
  expect(container.querySelector('script, img, iframe')).toBeNull();
  expect(container.innerHTML).not.toContain('href="javascript:');
  expect(screen.getByText('Safe content')).toBeInTheDocument();
});

it('translates network failures without exposing transport details', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockRejectedValue(new Error('low-level secret')),
  );
  await expect(api('/api/v1/reviews')).rejects.toBeInstanceOf(ApiError);
  await expect(api('/api/v1/reviews')).rejects.toMatchObject({
    code: 'network_failure',
    status: 0,
  });
});
