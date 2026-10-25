package demo.llm.client;

import static org.junit.jupiter.api.Assertions.*;

import demo.llm.generated.*;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.net.ConnectException;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.*;
import org.apache.thrift.protocol.TBinaryProtocol;
import org.apache.thrift.transport.TSocket;
import org.apache.thrift.transport.TTransportException;
import org.apache.thrift.transport.layered.TFramedTransport;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;

@Timeout(15)
class LlmClientTest {
    private static final int FRAME_BYTES = 2 * 1024 * 1024;

    @Test
    void allMethodsPreserveGeneratedValuesAndCloseEachConnection() throws Exception {
        try (Peer peer = processorPeer(new Handler("instance-a"))) {
            LlmClient client = peer.client(1000, FRAME_BYTES);
            assertEquals("instance-a", client.getModelInfo().instance_id);
            assertTrue(client.getHealth().ready);
            GenerationRequest request = request("instance-a", "request-a");
            assertEquals(GenerationState.QUEUED, client.startGeneration(request).state);
            assertEquals("request-a", client.getGeneration("instance-a", "request-a").request_id);
            assertEquals(GenerationState.CANCELLING,
                    client.cancelGeneration("instance-a", "request-a").state);
            peer.assertPeerClosed(5);
        }
    }

    @Test
    void remoteStructuredErrorIsNotWrappedAndConnectionIsClosed() throws Exception {
        Handler handler = new Handler("instance-a") {
            @Override
            public GenerationSnapshot getGeneration(String instance, String id) throws LlmError {
                throw new LlmError("not_found", "No retained task", false);
            }
        };
        try (Peer peer = processorPeer(handler)) {
            LlmError error = assertThrows(LlmError.class,
                    () -> peer.client(1000, FRAME_BYTES).getGeneration("instance-a", "missing"));
            assertEquals("not_found", error.code);
            assertEquals("No retained task", error.message);
            assertFalse(error.retryable);
            peer.assertPeerClosed(1);
        }
    }

    @Test
    void connectionRefusalPreservesTransportTypeAndCause() throws Exception {
        int unusedPort;
        try (ServerSocket reserved = new ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))) {
            unusedPort = reserved.getLocalPort();
        }
        LlmClient client = new LlmClient(LlmClientConfig.defaults("127.0.0.1", unusedPort));
        TTransportException error = assertThrows(TTransportException.class, client::getHealth);
        assertEquals(TTransportException.NOT_OPEN, error.getType());
        assertInstanceOf(ConnectException.class, error.getCause());
    }

    @Test
    void readTimeoutClosesSocketAndDoesNotRetryAcceptedSubmission() throws Exception {
        CountDownLatch accepted = new CountDownLatch(1);
        Handler handler = new Handler("instance-a") {
            @Override
            public GenerationSnapshot startGeneration(GenerationRequest request) {
                accepted.countDown();
                return super.startGeneration(request);
            }
        };
        // Process the real request, but intentionally lose the serialized response.
        try (Peer peer = new Peer(socket -> {
            try (TFramedTransport transport = framed(socket)) {
                TBinaryProtocol input = new TBinaryProtocol(transport, true, true);
                org.apache.thrift.transport.TMemoryBuffer sink =
                        new org.apache.thrift.transport.TMemoryBuffer(1024);
                new LlmService.Processor<>(handler).process(
                        input, new TBinaryProtocol(sink, true, true));
                expectEof(socket);
            }
        })) {
            TTransportException error = assertThrows(TTransportException.class,
                    () -> peer.client(150, FRAME_BYTES).startGeneration(request("instance-a", "lost")));
            assertEquals(TTransportException.TIMED_OUT, error.getType());
            assertInstanceOf(SocketTimeoutException.class, error.getCause());
            assertTrue(accepted.await(1, TimeUnit.SECONDS));
            peer.assertPeerClosed(1);
            assertEquals(1, peer.acceptedCount());
        }
    }

    @Test
    void oversizedResponseIsRejectedAndConnectionIsClosed() throws Exception {
        try (Peer peer = new Peer(socket -> {
            readRequest(socket);
            DataOutputStream output = new DataOutputStream(socket.getOutputStream());
            output.writeInt(1025);
            output.flush();
            expectEof(socket);
        })) {
            TTransportException error = assertThrows(TTransportException.class,
                    () -> peer.client(1000, 1024).getHealth());
            assertEquals(TTransportException.CORRUPTED_DATA, error.getType());
            peer.assertPeerClosed(1);
        }
    }

    @Test
    void oversizedRequestIsRejectedBeforeSendingAndConnectionIsClosed() throws Exception {
        try (Peer peer = new Peer(LlmClientTest::expectEof)) {
            GenerationRequest request = request("instance-a", "too-big");
            request.messages.get(0).setContent("x".repeat(2048));
            TTransportException error = assertThrows(TTransportException.class,
                    () -> peer.client(1000, 1024).startGeneration(request));
            assertEquals(TTransportException.MESSAGE_SIZE_LIMIT, error.getType());
            peer.assertPeerClosed(1);
        }
    }

    @Test
    void protocolFailureAlsoClosesConnection() throws Exception {
        try (Peer peer = new Peer(socket -> {
            readRequest(socket);
            // Strict read must reject this non-versioned message header.
            DataOutputStream output = new DataOutputStream(socket.getOutputStream());
            output.writeInt(4);
            output.writeInt(0);
            output.flush();
            expectEof(socket);
        })) {
            assertThrows(org.apache.thrift.protocol.TProtocolException.class,
                    () -> peer.client(1000, FRAME_BYTES).getHealth());
            peer.assertPeerClosed(1);
        }
    }

    @Test
    void sharedSdkAndSeparateConfigurationsDoNotMixConcurrentResponses() throws Exception {
        CyclicBarrier overlap = new CyclicBarrier(8);
        Handler handler = new Handler("instance-a") {
            @Override
            public GenerationSnapshot getGeneration(String instance, String id) throws LlmError {
                try {
                    overlap.await(3, TimeUnit.SECONDS);
                } catch (Exception error) {
                    throw new AssertionError(error);
                }
                return super.getGeneration(instance, id);
            }
        };
        try (Peer first = processorPeer(handler);
                Peer second = processorPeer(new Handler("instance-b"))) {
            LlmClient shared = first.client(4000, FRAME_BYTES);
            LlmClient other = second.client(1000, FRAME_BYTES);
            ExecutorService callers = Executors.newFixedThreadPool(8);
            try {
                List<Future<String>> replies = new ArrayList<>();
                for (int i = 0; i < 8; i++) {
                    String id = "parallel-" + i;
                    replies.add(callers.submit(() -> {
                        GenerationSnapshot value = shared.getGeneration("instance-a", id);
                        assertEquals("instance-a", value.instance_id);
                        return value.request_id;
                    }));
                }
                assertEquals("instance-b", other.getModelInfo().instance_id);
                for (int i = 0; i < 8; i++) {
                    assertEquals("parallel-" + i, replies.get(i).get(5, TimeUnit.SECONDS));
                }
                first.assertPeerClosed(8);
                second.assertPeerClosed(1);
            } finally {
                callers.shutdownNow();
            }
        }
    }

    @Test
    void invalidConfigurationCannotAccidentallySelectInfiniteTimeouts() {
        assertThrows(IllegalArgumentException.class,
                () -> new LlmClientConfig(" ", 9091, 100, 100, 1024));
        assertThrows(IllegalArgumentException.class,
                () -> new LlmClientConfig("localhost", 65536, 100, 100, 1024));
        assertThrows(IllegalArgumentException.class,
                () -> new LlmClientConfig("localhost", 9091, 0, 100, 1024));
        assertThrows(IllegalArgumentException.class,
                () -> new LlmClientConfig("localhost", 9091, 100, 0, 1024));
        assertThrows(IllegalArgumentException.class,
                () -> new LlmClientConfig("localhost", 9091, 100, 100, 0));
    }

    private static GenerationRequest request(String instance, String id) {
        return new GenerationRequest(id, instance, List.of(new Message("user", "Hello")), 3000);
    }

    private static TFramedTransport framed(Socket socket) throws TTransportException {
        return new TFramedTransport(new TSocket(socket), FRAME_BYTES);
    }

    private static void readRequest(Socket socket) throws Exception {
        DataInputStream input = new DataInputStream(socket.getInputStream());
        int size = input.readInt();
        assertEquals(size, input.readNBytes(size).length);
    }

    private static void expectEof(Socket socket) throws Exception {
        assertEquals(-1, socket.getInputStream().read(), "caller must close its connection");
    }

    private static Peer processorPeer(LlmService.Iface handler) throws Exception {
        return new Peer(socket -> {
            try (TFramedTransport transport = framed(socket)) {
                TBinaryProtocol protocol = new TBinaryProtocol(transport, true, true);
                new LlmService.Processor<>(handler).process(protocol, protocol);
                expectEof(socket);
            }
        });
    }

    private static class Handler implements LlmService.Iface {
        private final String instance;

        Handler(String instance) { this.instance = instance; }

        @Override
        public ModelInfo getModelInfo() {
            return new ModelInfo(instance, "test-model", "test-revision", new GenerationOptions(),
                    new Limits(1, 8, 128, 600000, 300000, 64000, 8192, 1024));
        }

        @Override
        public Health getHealth() { return new Health(true, false, 0, 0); }

        @Override
        public GenerationSnapshot startGeneration(GenerationRequest request) {
            assertEquals(instance, request.instance_id);
            assertEquals(3000, request.deadline_ms);
            assertEquals("user", request.messages.get(0).role);
            return new GenerationSnapshot(request.request_id, instance, GenerationState.QUEUED);
        }

        @Override
        public GenerationSnapshot getGeneration(String current, String id) throws LlmError {
            assertEquals(instance, current);
            return new GenerationSnapshot(id, instance, GenerationState.SUCCEEDED);
        }

        @Override
        public GenerationSnapshot cancelGeneration(String current, String id) {
            assertEquals(instance, current);
            return new GenerationSnapshot(id, instance, GenerationState.CANCELLING);
        }
    }

    @FunctionalInterface
    private interface ConnectionAction { void run(Socket socket) throws Exception; }

    /** Real loopback peers verify EOF before idle expiry can conceal a client resource leak. */
    private static final class Peer implements AutoCloseable {
        private final ServerSocket listener = new ServerSocket(
                0, 128, InetAddress.getByName("127.0.0.1"));
        private final ExecutorService workers = Executors.newCachedThreadPool();
        private final BlockingQueue<Object> completions = new LinkedBlockingQueue<>();
        private final java.util.concurrent.atomic.AtomicInteger accepted =
                new java.util.concurrent.atomic.AtomicInteger();
        private final Thread acceptor;

        Peer(ConnectionAction action) throws Exception {
            acceptor = new Thread(() -> {
                while (!listener.isClosed()) {
                    try {
                        Socket socket = listener.accept();
                        accepted.incrementAndGet();
                        workers.submit(() -> {
                            try (Socket current = socket) {
                                current.setSoTimeout(5000);
                                action.run(current);
                                completions.add(Boolean.TRUE);
                            } catch (Throwable error) {
                                completions.add(error);
                            }
                        });
                    } catch (java.io.IOException error) {
                        if (!listener.isClosed()) { completions.add(error); }
                    }
                }
            }, "llm-client-test-peer");
            acceptor.start();
        }

        LlmClient client(int readTimeout, int frameBytes) {
            return new LlmClient(new LlmClientConfig(
                    "127.0.0.1", listener.getLocalPort(), 1000, readTimeout, frameBytes));
        }

        int acceptedCount() { return accepted.get(); }

        void assertPeerClosed(int count) throws Exception {
            for (int i = 0; i < count; i++) {
                Object completion = completions.poll(6, TimeUnit.SECONDS);
                if (completion instanceof Throwable) {
                    throw new AssertionError("Peer failed", (Throwable) completion);
                }
                assertEquals(Boolean.TRUE, completion, "peer did not observe EOF");
            }
        }

        @Override
        public void close() throws Exception {
            listener.close();
            acceptor.join(1000);
            workers.shutdownNow();
            assertTrue(workers.awaitTermination(6, TimeUnit.SECONDS));
        }
    }
}
