package demo.llm.client;

import demo.llm.generated.GenerationRequest;
import demo.llm.generated.GenerationSnapshot;
import demo.llm.generated.Health;
import demo.llm.generated.LlmError;
import demo.llm.generated.LlmService;
import demo.llm.generated.ModelInfo;
import java.util.Objects;
import org.apache.thrift.TConfiguration;
import org.apache.thrift.TException;
import org.apache.thrift.protocol.TBinaryProtocol;
import org.apache.thrift.transport.TSocket;
import org.apache.thrift.transport.TTransportException;
import org.apache.thrift.transport.layered.TFramedTransport;

/**
 * Synchronous client with one independent TCP connection per RPC, closed before returning.
 * Instances may be shared between threads; mutable generated request/response objects must
 * remain owned by the caller. No method retries, polls, or cancels work implicitly.
 *
 * <p>Remote {@link LlmError}, transport, protocol and application exceptions are propagated
 * unchanged. A transport failure during submission does not prove the task was rejected:
 * reconcile with {@link #getGeneration(String, String)} using the original IDs.
 */
public final class LlmClient implements LlmService.Iface {
    private final LlmClientConfig config;

    public LlmClient(LlmClientConfig config) {
        this.config = Objects.requireNonNull(config, "config");
    }

    @Override
    public ModelInfo getModelInfo() throws TException {
        return rpc(LlmService.Client::getModelInfo);
    }

    @Override
    public Health getHealth() throws TException {
        return rpc(LlmService.Client::getHealth);
    }

    /** Submit once. The request's deadline_ms is independent of both socket timeouts. */
    @Override
    public GenerationSnapshot startGeneration(GenerationRequest request) throws LlmError, TException {
        return rpc(client -> client.startGeneration(request));
    }

    @Override
    public GenerationSnapshot getGeneration(String instanceId, String requestId)
            throws LlmError, TException {
        return rpc(client -> client.getGeneration(instanceId, requestId));
    }

    /** CANCELLING is not terminal; keep querying until native execution has exited. */
    @Override
    public GenerationSnapshot cancelGeneration(String instanceId, String requestId)
            throws LlmError, TException {
        return rpc(client -> client.cancelGeneration(instanceId, requestId));
    }

    @FunctionalInterface
    private interface Call<T> {
        T run(LlmService.Client client) throws TException;
    }

    private <T> T rpc(Call<T> operation) throws TException {
        // Never share Thrift's mutable TConfiguration.DEFAULT or a transport between calls.
        TConfiguration wireConfig = new TConfiguration();
        // The outer resource also covers failures while constructing the framed transport.
        try (TSocket socket = new TSocket(wireConfig, config.getHost(), config.getPort())) {
            socket.setConnectTimeout(config.getConnectTimeoutMillis());
            socket.setSocketTimeout(config.getReadTimeoutMillis());
            try (TFramedTransport transport =
                    new BoundedFramedTransport(socket, config.getMaxFrameBytes())) {
                transport.open();
                return operation.run(new LlmService.Client(
                        new TBinaryProtocol(transport, true, true)));
            }
        }
    }

    /** Thrift bounds incoming frames itself; also bound requests before they reach the wire. */
    private static final class BoundedFramedTransport extends TFramedTransport {
        private final int maxFrameBytes;
        private int written;

        BoundedFramedTransport(TSocket socket, int maxFrameBytes) throws TTransportException {
            super(socket, maxFrameBytes);
            this.maxFrameBytes = maxFrameBytes;
        }

        @Override
        public void write(byte[] bytes, int offset, int length) throws TTransportException {
            if (length > maxFrameBytes - written) {
                throw new TTransportException(TTransportException.MESSAGE_SIZE_LIMIT,
                        "Request exceeds maxFrameBytes");
            }
            super.write(bytes, offset, length);
            written += length;
        }

        @Override
        public void flush() throws TTransportException {
            try {
                super.flush();
            } finally {
                written = 0;
            }
        }
    }
}
