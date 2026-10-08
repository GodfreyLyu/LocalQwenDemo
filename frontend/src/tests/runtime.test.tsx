import '../test-support/mock-editor';
import App from '../App';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import {
  runtimeInfo,
  review,
  response,
  mockWorkspace,
} from '../test-support/workspace';

it('keeps model details collapsed and the runtime bar compact while idle', async () => {
  mockWorkspace();
  render(<App />);

  expect(await screen.findByText('Ready')).toBeInTheDocument();
  expect(screen.getByText(/Model: Qwen\/Qwen3-1.7B/)).not.toBeVisible();
  fireEvent.click(screen.getByText('About this instance'));
  expect(screen.getByText(/Model: Qwen\/Qwen3-1.7B/)).toBeVisible();
  expect(screen.getByText('Submit code to see the review here.')).toBeVisible();
  expect(
    screen.queryByText(/Rough model-time estimate:/),
  ).not.toBeInTheDocument();
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

it.each([
  ['minikube', 'Local minikube', 'real', 'Real model'],
  [
    'development',
    'Local development',
    'simulated',
    'Simulated model (no Qwen inference)',
  ],
  ['unknown', 'Environment unknown', 'unknown', 'Inference mode unknown'],
])(
  'separates %s deployment from %s inference',
  async (environment, environmentText, mode, modeText) => {
    mockWorkspace((path) =>
      path === '/api/v1/runtime'
        ? response({
            ...runtimeInfo,
            deployment_environment: environment,
            inference_mode: mode,
          })
        : undefined,
    );
    render(<App />);
    expect(await screen.findByText(modeText)).toBeInTheDocument();
    expect(screen.getAllByText(environmentText).length).toBeGreaterThan(0);
    expect(screen.queryByText('CUSTOMER DEMO')).not.toBeInTheDocument();
  },
);

it('preserves input through loading, disconnect and recovery without submitting', async () => {
  let current: Response | Error = response({
    ...runtimeInfo,
    service_status: 'model_loading',
    accepting_submissions: false,
  });
  const fetcher = mockWorkspace((path) =>
    path === '/api/v1/runtime'
      ? current instanceof Error
        ? Promise.reject(current)
        : current.clone()
      : undefined,
  );
  render(<App />);
  const editor = await screen.findByLabelText('Source code');
  await waitFor(() => expect(editor).not.toBeDisabled());
  fireEvent.change(editor, { target: { value: 'keep this snippet' } });
  const run = screen.getByRole('button', { name: 'Run review' });
  expect(run).toBeDisabled();
  expect(
    screen.getByRole('status', { name: 'Service status' }),
  ).toHaveTextContent('Model loading');
  expect(
    screen.getByRole('status', { name: 'Review task status' }),
  ).toHaveTextContent('No active review');
  current = new Error('disconnected');
  await waitFor(
    () =>
      expect(
        screen.getByRole('status', { name: 'Service status' }),
      ).toHaveTextContent('Connection interrupted'),
    { timeout: 6500 },
  );
  expect(editor).toHaveValue('keep this snippet');
  expect(run).toBeDisabled();
  current = response(runtimeInfo);
  await waitFor(() => expect(run).not.toBeDisabled(), { timeout: 6500 });
  expect(editor).toHaveValue('keep this snippet');
  expect(
    fetcher.mock.calls.filter(([, options]) => options?.method === 'POST'),
  ).toHaveLength(0);
}, 15000);

it.each([
  {
    ...runtimeInfo,
    service_status: 'new_future_state',
    accepting_submissions: true,
  },
  { unexpected: 'payload' },
  { ...runtimeInfo, service_status: 'ready', accepting_submissions: false },
])(
  'never enables submission for unknown or contradictory runtime data',
  async (data) => {
    mockWorkspace((path) =>
      path === '/api/v1/runtime' ? response(data) : undefined,
    );
    render(<App />);
    const editor = await screen.findByLabelText('Source code');
    await waitFor(() => expect(editor).not.toBeDisabled());
    fireEvent.change(editor, { target: { value: 'x = 1' } });
    expect(screen.getByRole('button', { name: 'Run review' })).toBeDisabled();
    expect(
      screen.getByRole('status', { name: 'Service status' }),
    ).toHaveTextContent('Status unknown');
  },
);

it('labels simulated execution and stored results, without real-model timing claims', async () => {
  let finish!: (value: Response) => void;
  const detail = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const fetcher = mockWorkspace((path, options) => {
    if (path === '/api/v1/runtime')
      return response({
        ...runtimeInfo,
        deployment_environment: 'development',
        inference_mode: 'simulated',
        model_id: 'Simulated model',
        model_revision: 'fixture-v1',
        model_source: 'test_fixture',
        device: null,
      });
    if (path === '/api/v1/reviews' && options?.method === 'POST')
      return response({ review_id: 'review-1', status: 'running' }, 202);
    if (path === '/api/v1/reviews/review-1') return detail;
  });
  render(<App />);
  const editor = await screen.findByLabelText('Source code');
  await waitFor(() => expect(editor).not.toBeDisabled());
  fireEvent.change(editor, { target: { value: 'x = 1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run review' }));
  expect(
    await screen.findByText(
      'Generating a deterministic test result. No real Qwen inference is running.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(/Rough model-time estimate:/),
  ).not.toBeInTheDocument();
  // The running label can render before the detail-polling effect starts.
  await waitFor(() =>
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/reviews/review-1',
      expect.any(Object),
    ),
  );
  finish(
    response({
      ...review,
      model_id: 'Simulated model',
      model_revision: 'fixture-v1',
    }),
  );
  expect(
    await screen.findByText('Simulated result · no Qwen inference'),
  ).toBeInTheDocument();
});
