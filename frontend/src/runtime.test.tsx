import { AboutInstance } from './RuntimePanel';
import {
  act,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react';
import { RUNTIME_POLL_MS, useRuntime } from './runtime';

it('never overlaps polling and aborts requests and scheduled work on unmount', async () => {
  vi.useFakeTimers();
  try {
    let finish: (value: Response) => void = () => {};
    let signal: AbortSignal | null | undefined;
    const fetcher = vi.fn((_path, options: RequestInit) => {
      signal = options.signal;
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    });
    vi.stubGlobal('fetch', fetcher);
    const { result, unmount } = renderHook(useRuntime);
    expect(result.current.connection).toBe('checking');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUNTIME_POLL_MS);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish(new Response('{}'));
    });
    expect(result.current.connection).toBe('unknown');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUNTIME_POLL_MS);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      finish(new Response('{}'));
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  } finally {
    vi.useRealTimers();
  }
});

it('times out a hung request, shows disconnection, and recovers on the next poll', async () => {
  vi.useFakeTimers();
  try {
    const fetcher = vi.fn(
      (_path, options: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          options.signal?.addEventListener('abort', () =>
            reject(new Error('timeout')),
          );
        }),
    );
    vi.stubGlobal('fetch', fetcher);
    // Use a controlled timeout signal because jsdom uses Node's native timeout clock.
    vi.spyOn(AbortSignal, 'timeout').mockImplementation((ms) => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(), ms);
      return controller.signal;
    });
    const { result, unmount } = renderHook(useRuntime);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(result.current.connection).toBe('disconnected');
    fetcher.mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            deployment_environment: 'development',
            inference_mode: 'simulated',
            service_status: 'ready',
            accepting_submissions: true,
            model_id: 'Simulated model',
            model_revision: 'fixture-v1',
            model_source: 'test_fixture',
            device: null,
          }),
        ),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RUNTIME_POLL_MS);
    });
    expect(result.current.connection).toBe('connected');
    expect(result.current.info?.accepting_submissions).toBe(true);
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  } finally {
    vi.useRealTimers();
  }
});

it('accepts observed Ollama identity and labels digest, quantization and device', async () => {
  const info = {
    deployment_environment: 'minikube' as const,
    inference_mode: 'real' as const,
    service_status: 'ready',
    accepting_submissions: true,
    model_id: 'qwen3:1.7b',
    model_revision: 'sha256:' + 'a'.repeat(64),
    model_source: 'ollama_api' as const,
    device: 'gpu' as const,
    inference_backend: 'ollama' as const,
    quantization: 'Q4_K_M',
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(info))),
  );
  const { result, unmount } = renderHook(useRuntime);
  await waitFor(() => expect(result.current.connection).toBe('connected'));
  render(<AboutInstance runtime={result.current} />);
  expect(screen.getByText(/Real model via local Ollama/)).toHaveTextContent(
    'gpu',
  );
  expect(screen.getByText(/Model: qwen3/)).toHaveTextContent('Digest:');
  expect(screen.getByText(/Model: qwen3/)).toHaveTextContent('Q4_K_M');
  expect(screen.queryByText(/Real model on local CPU/)).not.toBeInTheDocument();
  unmount();
});
