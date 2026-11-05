import demo.llm.generated.GenerationOptions;
import demo.llm.generated.GenerationRequest;
import demo.llm.generated.GenerationSnapshot;
import demo.llm.generated.GenerationState;
import demo.llm.generated.LlmError;
import demo.llm.generated.LlmService;
import demo.llm.generated.Message;
import demo.llm.generated.ModelInfo;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.apache.thrift.TConfiguration;
import org.apache.thrift.TException;
import org.apache.thrift.protocol.TBinaryProtocol;
import org.apache.thrift.transport.TSocket;
import org.apache.thrift.transport.TTransportException;
import org.apache.thrift.transport.layered.TFramedTransport;

/** Direct generated-client assertions driven by the Python TCP test fixture. */
public final class InteropProbe {
    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(args[0]);
        String mode = args[1];
        int timeout = mode.equals("lost") ? 200 : 3000;
        ModelInfo info = rpc(port, timeout, LlmService.Client::getModelInfo);
        check(rpc(port, timeout, LlmService.Client::getHealth).ready, "health");
        GenerationRequest request = new GenerationRequest("java-" + mode, info.instance_id,
                List.of(new Message("user", "Hello")), 3000);
        request.setOptions(new GenerationOptions().setSeed(Long.MAX_VALUE).setTemperature(0));
        if (mode.equals("lost")) {
            try {
                rpc(port, timeout, client -> client.startGeneration(request));
                throw new AssertionError("submission response should be lost");
            } catch (TTransportException error) {
                check(error.getType() == TTransportException.TIMED_OUT, "read timeout type");
            }
            GenerationSnapshot recovered = rpc(port, timeout,
                    client -> client.getGeneration(info.instance_id, request.request_id));
            check(recovered.request_id.equals(request.request_id), "recovered ID");
            check(recovered.state == GenerationState.RUNNING, "accepted work still running");
        } else if (mode.equals("lifecycle")) {
            check(rpc(port, timeout, client -> client.startGeneration(request)).state
                    == GenerationState.QUEUED, "admission");
            GenerationSnapshot result = rpc(port, timeout,
                    client -> client.getGeneration(info.instance_id, request.request_id));
            check(result.state == GenerationState.SUCCEEDED, "completion");
            check(result.result.content.equals("Bonjour"), "content");
            check(result.result.usage.output_tokens == 2, "usage");
            check(result.expires_at_ms == 4102444800000L, "i64 expiry");
            try {
                rpc(port, timeout, client -> client.getGeneration(info.instance_id, "missing"));
                throw new AssertionError("expected remote LlmError");
            } catch (LlmError error) {
                check(error.code.equals("not_found") && !error.retryable, "structured error");
            }
            GenerationSnapshot cancelling = rpc(port, timeout,
                    client -> client.cancelGeneration(info.instance_id, "cancel-me"));
            check(cancelling.state == GenerationState.CANCELLING, "nonterminal cancellation");
            check(rpc(port, timeout, client -> client.getGeneration(info.instance_id, "cancel-me"))
                    .state == GenerationState.CANCELLED, "confirmed native exit");
        } else if (mode.equals("concurrent")) {
            ExecutorService callers = Executors.newFixedThreadPool(8);
            try {
                List<Future<GenerationSnapshot>> replies = new ArrayList<>();
                for (int i = 0; i < 8; i++) {
                    String id = "parallel-" + i;
                    replies.add(callers.submit(() -> rpc(port, timeout,
                            client -> client.getGeneration(info.instance_id, id))));
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

    @FunctionalInterface
    private interface Call<T> {
        T run(LlmService.Client client) throws TException;
    }

    private static <T> T rpc(int port, int timeout, Call<T> call) throws TException {
        // The fixture observes EOF after each call, including timeout and remote-error paths.
        try (TSocket socket = new TSocket(new TConfiguration(), "127.0.0.1", port)) {
            socket.setConnectTimeout(1000);
            socket.setSocketTimeout(timeout);
            try (TFramedTransport transport = new TFramedTransport(socket, 2 * 1024 * 1024)) {
                transport.open();
                return call.run(new LlmService.Client(new TBinaryProtocol(transport, true, true)));
            }
        }
    }

    private static void check(boolean success, String message) {
        if (!success) { throw new AssertionError(message); }
    }
}
