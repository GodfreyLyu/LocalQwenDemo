package demo.llm.client;

import demo.llm.generated.*;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.*;
import org.apache.thrift.transport.TTransportException;

/** Assertions driven by the Python TCP test fixture; excluded from the library JAR. */
public final class InteropProbe {
    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(args[0]);
        String mode = args[1];
        LlmClient client = new LlmClient(new LlmClientConfig("127.0.0.1", port, 1000,
                mode.equals("lost") ? 200 : 3000, 2 * 1024 * 1024));
        ModelInfo info = client.getModelInfo();
        check(client.getHealth().ready, "health");
        GenerationRequest request = new GenerationRequest("java-" + mode, info.instance_id,
                List.of(new Message("user", "Hello")), 3000);
        request.setOptions(new GenerationOptions().setSeed(Long.MAX_VALUE).setTemperature(0));
        if (mode.equals("lost")) {
            try {
                client.startGeneration(request);
                throw new AssertionError("submission response should be lost");
            } catch (TTransportException error) {
                check(error.getType() == TTransportException.TIMED_OUT, "read timeout type");
            }
            GenerationSnapshot recovered = client.getGeneration(info.instance_id, request.request_id);
            check(recovered.request_id.equals(request.request_id), "recovered ID");
            check(recovered.state == GenerationState.RUNNING, "accepted work still running");
        } else if (mode.equals("lifecycle")) {
            check(client.startGeneration(request).state == GenerationState.QUEUED, "admission");
            GenerationSnapshot result = client.getGeneration(info.instance_id, request.request_id);
            check(result.state == GenerationState.SUCCEEDED, "completion");
            check(result.result.content.equals("Bonjour"), "content");
            check(result.result.usage.output_tokens == 2, "usage");
            check(result.expires_at_ms == 4102444800000L, "i64 expiry");
            try {
                client.getGeneration(info.instance_id, "missing");
                throw new AssertionError("expected remote LlmError");
            } catch (LlmError error) {
                check(error.code.equals("not_found") && !error.retryable, "structured error");
            }
            GenerationSnapshot cancelling = client.cancelGeneration(info.instance_id, "cancel-me");
            check(cancelling.state == GenerationState.CANCELLING, "nonterminal cancellation");
            check(client.getGeneration(info.instance_id, "cancel-me").state
                    == GenerationState.CANCELLED, "confirmed native exit");
        } else if (mode.equals("concurrent")) {
            ExecutorService callers = Executors.newFixedThreadPool(8);
            try {
                List<Future<GenerationSnapshot>> replies = new ArrayList<>();
                for (int i = 0; i < 8; i++) {
                    String id = "parallel-" + i;
                    replies.add(callers.submit(() -> client.getGeneration(info.instance_id, id)));
                }
                for (int i = 0; i < 8; i++) {
                    GenerationSnapshot value = replies.get(i).get(5, TimeUnit.SECONDS);
                    check(value.request_id.equals("parallel-" + i), "concurrent ID");
                    check(value.instance_id.equals(info.instance_id), "concurrent instance");
                }
            } finally {
                callers.shutdownNow();
            }
        } else {
            throw new IllegalArgumentException(mode);
        }
        System.out.println("ok=" + mode);
    }

    private static void check(boolean success, String message) {
        if (!success) { throw new AssertionError(message); }
    }
}
