import '../test-support/mock-editor';
import App from '../App';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { api, ApiError } from '../api';
import { review, response, mockWorkspace } from '../test-support/workspace';

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
  const run = screen.getByRole('button', { name: 'Run review' });
  fireEvent.click(run);
  fireEvent.click(run);
  expect(
    screen.queryByText(/Rough model-time estimate:/),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole('status', { name: 'Review task status' }),
  ).toHaveTextContent('Submitting your review');
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
  expect(
    screen.getByRole('status', { name: 'Review task status' }),
  ).toHaveTextContent('Review complete');
  expect(
    screen.queryByText(/Rough model-time estimate:/),
  ).not.toBeInTheDocument();
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
  expect(screen.getByRole('button', { name: 'Run review' })).toBeDisabled();
  fireEvent.change(editor, { target: { value: 'x'.repeat(12001) } });
  expect(screen.getByRole('button', { name: 'Run review' })).toBeDisabled();
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
  fireEvent.click(screen.getByRole('button', { name: 'Run review' }));
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
  fireEvent.click(screen.getByRole('button', { name: 'Run review' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'model returned an unusable review',
  );
  expect(
    screen.getByRole('status', { name: 'Review task status' }),
  ).toHaveTextContent('Review could not be completed');
  expect(
    screen.queryByText(/Rough model-time estimate:/),
  ).not.toBeInTheDocument();
  expect(editor).not.toBeDisabled();
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

it('keeps server readiness authoritative and does not automatically retry a rejected submission', async () => {
  const fetcher = mockWorkspace((path, options) =>
    path === '/api/v1/reviews' && options?.method === 'POST'
      ? response(
          { error: { code: 'model_loading', message: 'Not ready' } },
          503,
        )
      : undefined,
  );
  render(<App />);
  const editor = await screen.findByLabelText('Source code');
  await waitFor(() => expect(editor).not.toBeDisabled());
  fireEvent.change(editor, { target: { value: 'x = 1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run review' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'local model service is preparing',
  );
  expect(editor).not.toBeDisabled();
  expect(
    fetcher.mock.calls.filter(([, options]) => options?.method === 'POST'),
  ).toHaveLength(1);
});

it('retries uncertain delivery only explicitly, with the same frozen input and idempotency key', async () => {
  let attempts = 0;
  const fetcher = mockWorkspace((path, options) =>
    path === '/api/v1/reviews' && options?.method === 'POST'
      ? ++attempts === 1
        ? Promise.reject(new Error('lost response'))
        : response({ review_id: 'review-1', status: 'queued' }, 202)
      : undefined,
  );
  render(<App />);
  const editor = await screen.findByLabelText('Source code');
  await waitFor(() => expect(editor).not.toBeDisabled());
  fireEvent.change(editor, { target: { value: 'preserve original' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run review' }));
  const retry = await screen.findByRole('button', {
    name: 'Retry same submission',
  });
  expect(editor).toHaveValue('preserve original');
  expect(editor).toBeDisabled();
  expect(attempts).toBe(1);
  fireEvent.click(retry);
  expect(await screen.findByText('A useful suggestion.')).toBeInTheDocument();
  const posts = fetcher.mock.calls.filter(
    ([, options]) => options?.method === 'POST',
  );
  expect(posts).toHaveLength(2);
  expect(posts[0][1]?.body).toBe(posts[1][1]?.body);
  expect(
    JSON.parse(posts[0][1]?.body as string).client_request_id,
  ).toBeTruthy();
});
