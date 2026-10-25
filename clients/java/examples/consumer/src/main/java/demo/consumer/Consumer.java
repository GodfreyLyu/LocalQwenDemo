package demo.consumer;

import demo.llm.client.LlmClient;
import demo.llm.client.LlmClientConfig;
import demo.llm.generated.GenerationRequest;
import demo.llm.generated.GenerationSnapshot;
import demo.llm.generated.GenerationState;
import demo.llm.generated.Health;
import demo.llm.generated.LlmError;
import demo.llm.generated.Message;
import demo.llm.generated.ModelInfo;
import java.util.List;
import java.util.UUID;
import org.apache.thrift.transport.TTransportException;

/** Copy this Maven project anywhere after installing the SDK; no repository sources are needed. */
public final class Consumer {
    public static void main(String[] args) throws Exception {
        String host = args.length > 0 ? args[0] : "127.0.0.1";
        int port = args.length > 1 ? Integer.parseInt(args[1]) : 9091;
        // "cancel" runs a second task and demonstrates explicit cancellation.
        boolean cancel = args.length > 2 && args[2].equals("cancel");
        LlmClient client = new LlmClient(new LlmClientConfig(host, port, 1000, 2000, 2 * 1024 * 1024));
        ModelInfo info = client.getModelInfo();
        Health health = client.getHealth();
        if (!health.ready || health.draining) {
            throw new IllegalStateException("Service is not accepting work");
        }
        System.out.println("model=" + info.model_id + " revision=" + info.model_revision);
        runTask(client, info, false);
        if (cancel) {
            runTask(client, info, true);
        }
    }

    private static void runTask(LlmClient client, ModelInfo info, boolean cancel) throws Exception {
        // Keep the original IDs and payload for the entire lifetime, including uncertain outcomes.
        String id = UUID.randomUUID().toString();
        GenerationRequest request = new GenerationRequest(id, info.instance_id,
                List.of(new Message("user", "Translate Hello to French.")),
                Math.min(3000, info.limits.max_deadline_ms));
        System.out.println("request_id=" + id + " instance_id=" + info.instance_id);
        GenerationSnapshot snapshot;
        try {
            snapshot = client.startGeneration(request);
        } catch (TTransportException uncertain) {
            // Do not call startGeneration again. A failed lookup must remain an uncertain outcome.
            try {
                snapshot = client.getGeneration(info.instance_id, id);
            } catch (Exception lookupFailed) {
                lookupFailed.addSuppressed(uncertain);
                throw lookupFailed;
            }
        } catch (LlmError rejected) {
            System.err.println("submission error=" + rejected.code + " retryable=" + rejected.retryable);
            throw rejected;
        }

        // This local polling budget neither stops generation nor replaces deadline_ms.
        long stopPollingAt = System.nanoTime() + java.util.concurrent.TimeUnit.SECONDS.toNanos(10);
        if (cancel && !terminal(snapshot.state)) {
            snapshot = client.cancelGeneration(info.instance_id, id);
        }
        while (!terminal(snapshot.state)) {
            if (System.nanoTime() >= stopPollingAt) {
                throw new IllegalStateException("Polling budget exhausted; reconcile task " + id);
            }
            Thread.sleep(100);
            snapshot = client.getGeneration(info.instance_id, id);
        }
        System.out.println("state=" + snapshot.state);
        if (snapshot.state == GenerationState.SUCCEEDED) {
            System.out.println(snapshot.result.content);
        } else if (snapshot.error != null) {
            // A terminal FAILED snapshot is distinct from an RPC throwing LlmError.
            System.err.println("task error=" + snapshot.error.code);
        }
    }

    private static boolean terminal(GenerationState state) {
        if (state == null) {
            throw new IllegalStateException("Unknown generation state; reconcile before taking action");
        }
        return state == GenerationState.SUCCEEDED || state == GenerationState.FAILED
                || state == GenerationState.CANCELLED;
    }
}
