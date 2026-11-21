package com.aipaas.platform.gateway;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.observability.TraceStore;
import com.aipaas.platform.observability.TracingService;
import com.sun.net.httpserver.HttpServer;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 管理请求代理：清理外部身份头并重建可信头，保体流式转发与上游错误映射。 */
class ManagementProxyControllerTest {

    private final Map<String, String> upstreamHeaders = new ConcurrentHashMap<>();
    private HttpServer server;
    private ManagementProxyController controller;

    @BeforeEach
    void setUp() throws IOException {
        upstreamHeaders.clear();
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            exchange.getRequestHeaders().forEach((name, values) ->
                    upstreamHeaders.put(name.toLowerCase(), String.join(",", values)));
            String path = exchange.getRequestURI().getPath();
            if (path.endsWith("/traces/stream")) {
                byte[] body = "data: {\"chunk\":1}\n\n".getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().add("Content-Type", "text/event-stream");
                exchange.sendResponseHeaders(200, body.length);
                exchange.getResponseBody().write(body);
            } else {
                byte[] body = "{\"code\":\"OK\",\"message\":\"OK\",\"data\":{}}".getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().add("Content-Type", "application/json");
                exchange.sendResponseHeaders(200, body.length);
                exchange.getResponseBody().write(body);
            }
            exchange.close();
        });
        server.start();
        controller = new ManagementProxyController("http://127.0.0.1:" + server.getAddress().getPort(),
                Duration.ofSeconds(5), Duration.ofSeconds(10), tracing());
        MDC.put("traceId", "trace-proxy-test");
        SecurityContextHolder.getContext().setAuthentication(new UsernamePasswordAuthenticationToken(
                new SecurityPrincipal("usr_9", "default", "admin", "Admin", "session-9",
                        Set.of("admin"), Set.of("user:read")), null, List.of()));
    }

    @AfterEach
    void tearDown() {
        MDC.clear();
        SecurityContextHolder.clearContext();
        server.stop(0);
    }

    @Test
    void externalIdentityHeadersAreDroppedAndTrustedOnesRegenerated() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/v1/users");
        request.setRequestURI("/api/v1/users");
        request.addHeader("Authorization", "Bearer incoming-token");
        request.addHeader("Idempotency-Key", "idem-1");
        request.addHeader("X-User-Id", "attacker");
        request.addHeader("X-Tenant-Id", "other");
        request.addHeader("X-Roles", "ADMIN");
        request.addHeader("X-Permissions", "*");
        request.addHeader("X-Kb-Ids", "kb-evil");
        request.addHeader("X-Security-Level", "9");
        request.addHeader("Connection", "keep-alive");
        request.setAttribute(GatewayJwtAuthenticationFilter.KB_IDS_ATTRIBUTE, List.of("kb-1", "kb-2"));

        ResponseEntity<?> response = controller.proxy(request);
        String body = new String((byte[]) response.getBody(), java.nio.charset.StandardCharsets.UTF_8);

        assertEquals(200, response.getStatusCode().value());
        assertTrue(body.contains("\"OK\""));
        assertEquals("usr_9", upstreamHeaders.get("x-user-id"));
        assertEquals("default", upstreamHeaders.get("x-tenant-id"));
        assertEquals("admin", upstreamHeaders.get("x-roles"));
        assertEquals("user:read", upstreamHeaders.get("x-permissions"));
        assertEquals("kb-1,kb-2", upstreamHeaders.get("x-kb-ids"));
        assertNull(upstreamHeaders.get("x-security-level"));
        assertEquals("Bearer incoming-token", upstreamHeaders.get("authorization"));
        assertEquals("idem-1", upstreamHeaders.get("idempotency-key"));
        assertEquals("trace-proxy-test", upstreamHeaders.get("x-request-id"));
        assertEquals("trace-proxy-test", upstreamHeaders.get("x-trace-id"));
        assertEquals("trace-proxy-test", response.getHeaders().getFirst("X-Trace-Id"));
    }

    @Test
    void sseUpstreamKeepsStreamingFlags() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/v1/traces/stream");
        request.setRequestURI("/api/v1/traces/stream");

        ResponseEntity<?> response = controller.proxy(request);
        String body = drain((StreamingResponseBody) response.getBody());

        assertEquals(200, response.getStatusCode().value());
        assertTrue(body.contains("data:"));
        assertEquals("no", response.getHeaders().getFirst("X-Accel-Buffering"));
        assertEquals("no-cache", response.getHeaders().getFirst("Cache-Control"));
        assertEquals(-1L, response.getHeaders().getContentLength());
    }

    @Test
    void unreachableManagementMapsToServiceUnavailable() {
        ManagementProxyController broken = new ManagementProxyController("http://127.0.0.1:1",
                Duration.ofMillis(300), Duration.ofMillis(300), tracing());
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/v1/users");
        request.setRequestURI("/api/v1/users");

        ApiException error = assertThrows(ApiException.class, () -> broken.proxy(request));

        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, error.getStatus());
        assertEquals(ErrorCodes.SYS_DEPENDENCY_UNAVAILABLE, error.getErrorCode());
        assertFalse(upstreamHeaders.containsKey("x-user-id"));
    }

    private static TracingService tracing() {
        return new TracingService(new TraceStore(1000), new SimpleMeterRegistry(), "platform-gateway");
    }

    private static String drain(StreamingResponseBody body) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        body.writeTo(output);
        return output.toString(StandardCharsets.UTF_8);
    }
}
