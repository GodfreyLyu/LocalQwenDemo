package demo.llm.client;

import java.util.Objects;

/** Immutable, per-client connection settings. Timeouts must be finite and positive. */
public final class LlmClientConfig {
    private final String host;
    private final int port;
    private final int connectTimeoutMillis;
    private final int readTimeoutMillis;
    private final int maxFrameBytes;

    /** Frame size excludes the four-byte frame header; it applies in both directions. */
    public LlmClientConfig(String host, int port, int connectTimeoutMillis,
            int readTimeoutMillis, int maxFrameBytes) {
        this.host = Objects.requireNonNull(host, "host");
        if (host.isBlank()) {
            throw new IllegalArgumentException("host must not be blank");
        }
        if (port < 1 || port > 65535) {
            throw new IllegalArgumentException("port must be between 1 and 65535");
        }
        if (connectTimeoutMillis <= 0 || readTimeoutMillis <= 0 || maxFrameBytes <= 0) {
            throw new IllegalArgumentException("timeouts and maxFrameBytes must be positive");
        }
        this.port = port;
        this.connectTimeoutMillis = connectTimeoutMillis;
        this.readTimeoutMillis = readTimeoutMillis;
        this.maxFrameBytes = maxFrameBytes;
    }

    /** Two-second connect/read timeouts and the service's current 2 MiB frame limit. */
    public static LlmClientConfig defaults(String host, int port) {
        return new LlmClientConfig(host, port, 2000, 2000, 2 * 1024 * 1024);
    }

    public String getHost() { return host; }
    public int getPort() { return port; }
    public int getConnectTimeoutMillis() { return connectTimeoutMillis; }
    public int getReadTimeoutMillis() { return readTimeoutMillis; }
    public int getMaxFrameBytes() { return maxFrameBytes; }
}
