import demo.llm.generated.*;
import java.util.List;
import java.util.UUID;
import org.apache.thrift.protocol.TBinaryProtocol;
import org.apache.thrift.transport.TSocket;
import org.apache.thrift.transport.layered.TFramedTransport;

/** Small Java client, not a Java application project. IDs work across languages. */
public class Generate {
    interface Call<T> { T run(LlmService.Client client) throws Exception; }
    static String host;
    static int port;

    static <T> T rpc(Call<T> operation) throws Exception {
        try (var transport = new TFramedTransport(new TSocket(host, port, 2000), 2 * 1024 * 1024)) {
            transport.open();
            return operation.run(new LlmService.Client(new TBinaryProtocol(transport, true, true)));
        }
    }

    public static void main(String[] args) throws Exception {
        host = args.length > 0 ? args[0] : "127.0.0.1";
        port = args.length > 1 ? Integer.parseInt(args[1]) : 9091;
        ModelInfo info = rpc(client -> client.getModelInfo());
        String id = args.length > 2 ? args[2] : UUID.randomUUID().toString();
        // Use exactly these values in the Python interoperability test as well.
        GenerationRequest request = new GenerationRequest(id, info.instance_id,
                List.of(new Message("user", "Translate Hello to French.")),
                Math.min(180000, info.limits.max_deadline_ms));
        request.setOptions(new GenerationOptions().setMax_output_tokens(32).setTemperature(0));
        System.out.println("request_id=" + id + " instance_id=" + info.instance_id);
        // Never retry start after a lost response: getGeneration(instance, id) reconciles it.
        GenerationSnapshot snapshot = rpc(client -> client.startGeneration(request));
        while (snapshot.state == GenerationState.QUEUED || snapshot.state == GenerationState.RUNNING
                || snapshot.state == GenerationState.CANCELLING) {
            Thread.sleep(100);
            snapshot = rpc(client -> client.getGeneration(info.instance_id, id));
        }
        if (snapshot.state != GenerationState.SUCCEEDED) {
            throw new IllegalStateException("Generation failed");
        }
        System.out.println(snapshot.result.content);
        System.out.println("revision=" + snapshot.result.model_revision + " output_tokens="
                + snapshot.result.usage.output_tokens + " finish_reason=" + snapshot.result.finish_reason);
    }
}
