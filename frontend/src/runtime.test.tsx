import { act, renderHook } from '@testing-library/react';
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
