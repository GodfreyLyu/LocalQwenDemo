import demo.llm.client.LlmClient;
import demo.llm.client.LlmClientConfig;
import demo.llm.generated.*;
import java.util.List;
import java.util.UUID;

/** External SDK example. Application prompts, polling and printing stay outside the library. */
public class Generate {
    public static void main(String[] args) throws Exception {
        String host = args.length > 0 ? args[0] : "127.0.0.1";
        int port = args.length > 1 ? Integer.parseInt(args[1]) : 9091;
        LlmClient client = new LlmClient(LlmClientConfig.defaults(host, port));
        ModelInfo info = client.getModelInfo();
        String id = args.length > 2 ? args[2] : UUID.randomUUID().toString();
        // Use exactly these values in the Python interoperability test as well.
        GenerationRequest request = new GenerationRequest(id, info.instance_id,
                List.of(new Message("user", "Translate Hello to French.")),
                Math.min(180000, info.limits.max_deadline_ms));
        request.setOptions(new GenerationOptions().setMax_output_tokens(32).setTemperature(0));
        System.out.println("request_id=" + id + " instance_id=" + info.instance_id);
        // Never retry start after a lost response: getGeneration(instance, id) reconciles it.
        GenerationSnapshot snapshot = client.startGeneration(request);
        while (snapshot.state == GenerationState.QUEUED || snapshot.state == GenerationState.RUNNING
                || snapshot.state == GenerationState.CANCELLING) {
            Thread.sleep(100);
            snapshot = client.getGeneration(info.instance_id, id);
        }
        if (snapshot.state != GenerationState.SUCCEEDED) {
            throw new IllegalStateException("Generation failed");
        }
        System.out.println(snapshot.result.content);
        System.out.println("revision=" + snapshot.result.model_revision + " output_tokens="
                + snapshot.result.usage.output_tokens + " finish_reason=" + snapshot.result.finish_reason);
    }
}
