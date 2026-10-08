import '../test-support/mock-editor';
import App from '../App';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { review, response, mockWorkspace } from '../test-support/workspace';

it('restores a persisted running review without a time estimate', async () => {
  let resolveDetail!: (value: Response) => void;
  const detail = new Promise<Response>((resolve) => {
    resolveDetail = resolve;
  });
  mockWorkspace((path) => {
    if (path === '/api/v1/reviews')
      return response({
        items: [{ ...review, status: 'running' }],
        next_cursor: null,
      });
    if (path === '/api/v1/reviews/review-1') return detail;
  });
  render(<App />);

  await waitFor(() =>
    expect(
      screen.getByRole('status', { name: 'Review task status' }),
    ).toHaveTextContent('Review in progress'),
  );
  expect(
    screen.queryByText(/Rough model-time estimate:/),
  ).not.toBeInTheDocument();
  expect(
    await screen.findByText(
      'Reviewing on local CPU. Time depends on code length and machine load.',
    ),
  ).toBeVisible();
  const editor = screen.getByLabelText('Source code');
  // History provides the running status before the detail request restores code.
  expect(editor).toHaveValue('');
  expect(editor).toBeDisabled();

  resolveDetail(
    response({
      ...review,
      status: 'running',
      source_code: 'x'.repeat(6000),
      review_result: null,
      model_id: null,
      model_revision: null,
    }),
  );
  await waitFor(() => expect(editor).toHaveValue('x'.repeat(6000)));
  expect(editor).toBeDisabled();
  expect(
    screen.queryByText(/Rough model-time estimate:/),
  ).not.toBeInTheDocument();
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

it('keeps history pagination, selection and new-review controls available', async () => {
  const older = {
    ...review,
    review_id: 'review-2',
    language: 'javascript',
    source_code: 'let x = 2;',
  };
  mockWorkspace((path) => {
    if (path === '/api/v1/reviews')
      return response({ items: [review], next_cursor: 'older-page' });
    if (path === '/api/v1/reviews?before=older-page')
      return response({ items: [older], next_cursor: null });
    if (path === '/api/v1/reviews/review-2') return response(older);
  });
  render(<App />);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Load older reviews' }),
  );
  const record = await screen.findByRole('button', {
    name: /javascript review/i,
  });
  await waitFor(() => expect(record).not.toBeDisabled());
  expect(screen.getByRole('button', { name: /python review/i })).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'Load older reviews' }),
  ).not.toBeInTheDocument();
  fireEvent.click(record);
  await waitFor(() =>
    expect(screen.getByLabelText('Source code')).toHaveValue(older.source_code),
  );
  expect(record).toHaveAttribute('aria-current', 'true');
  fireEvent.click(screen.getByRole('button', { name: 'New review' }));
  expect(screen.getByLabelText('Source code')).toHaveValue('');
  expect(screen.getByText('Submit code to see the review here.')).toBeVisible();
});
