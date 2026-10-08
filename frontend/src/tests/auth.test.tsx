import '../test-support/mock-editor';
import App from '../App';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { session, response, mockWorkspace } from '../test-support/workspace';

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
    await screen.findByRole('button', { name: 'Run review' }),
  ).toBeInTheDocument();
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
  fireEvent.click(screen.getByRole('button', { name: 'Run review' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Copy any unsaved code',
  );
  expect(editor).toHaveValue('unsaved code');
});

it('switches compact account forms without losing input or autocomplete and validation attributes', async () => {
  mockWorkspace((path) =>
    path.endsWith('/auth/me')
      ? response({ error: { message: 'Sign in' } }, 401)
      : undefined,
  );
  render(<App />);
  const username = await screen.findByLabelText('Username');
  const password = screen.getByLabelText('Password');
  fireEvent.change(username, { target: { value: 'local-user' } });
  fireEvent.change(password, { target: { value: 'a-valid-password' } });
  expect(username).toHaveAttribute('autocomplete', 'username');
  expect(username).toHaveAttribute('minlength', '3');
  expect(password).toHaveAttribute('minlength', '12');
  expect(password).toHaveAttribute('autocomplete', 'current-password');
  fireEvent.click(screen.getByRole('button', { name: 'Create an account' }));
  expect(screen.getByRole('heading', { name: 'Create account' })).toBeVisible();
  expect(password).toHaveAttribute('autocomplete', 'new-password');
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(screen.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  expect(username).toHaveValue('local-user');
  expect(password).toHaveValue('a-valid-password');
  expect(password).toHaveAttribute('autocomplete', 'current-password');
  expect(document.querySelector('.auth-story, .code-card')).toBeNull();
});

it('keeps form submission locked and reports authentication errors inline', async () => {
  let finish: (value: Response) => void = () => {};
  const fetcher = mockWorkspace((path) => {
    if (path.endsWith('/auth/me'))
      return response({ error: { message: 'Sign in' } }, 401);
    if (path.endsWith('/auth/login'))
      return new Promise((resolve) => {
        finish = resolve;
      });
  });
  render(<App />);
  fireEvent.change(await screen.findByLabelText('Username'), {
    target: { value: 'alice' },
  });
  fireEvent.change(screen.getByLabelText('Password'), {
    target: { value: 'a-valid-password' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(screen.getByLabelText('Username')).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Create an account' }),
  ).toBeDisabled();
  expect(document.querySelector('form')).toHaveAttribute('aria-busy', 'true');
  finish(
    response(
      {
        error: {
          code: 'invalid_credentials',
          message: 'Invalid username or password.',
        },
      },
      401,
    ),
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Invalid username or password.',
  );
  expect(screen.getByLabelText('Username')).toHaveValue('alice');
  expect(screen.getByRole('button', { name: 'Sign in' })).not.toBeDisabled();
  expect(
    fetcher.mock.calls.filter(([, options]) => options?.method === 'POST'),
  ).toHaveLength(1);
});
