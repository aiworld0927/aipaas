package com.aipaas.platform.chat;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.content.OutputComplianceGuard;
import com.aipaas.platform.gateway.GatewayAuditService;
import com.aipaas.platform.gateway.GatewayContentSecurityService;
import com.aipaas.platform.gateway.GatewayJwtAuthenticationFilter;
import com.aipaas.platform.observability.TracingService;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** 网关问答安全编排入口，直接调用 svc-query，不同步经过管理服务。 */
@RestController
@RequestMapping("/api/v1")
public class ChatController {

    private static final Logger log = LoggerFactory.getLogger(ChatController.class);
    private static final String ACTION_CHAT_QUERY = "chat.query";
    private static final int MAX_QUERY_CHARS = 2000;
    private static final int MAX_KB_ID_HEADER = 4096;

    private final GatewayContentSecurityService security;
    private final OutputComplianceGuard compliance;
    private final TracingService tracing;
    private final ObjectMapper objectMapper;
    private final GatewayAuditService audit;
    private final HttpClient client;
    private final String serviceUrl;
    private final Duration readTimeout;

    public ChatController(GatewayContentSecurityService security, OutputComplianceGuard compliance, TracingService tracing,
                          ObjectMapper objectMapper, GatewayAuditService audit,
                          @Value("${aipaas.chat.service-url:http://svc-query:8002}") String serviceUrl,
                          @Value("${aipaas.chat.connect-timeout:PT5S}") Duration connectTimeout,
                          @Value("${aipaas.chat.read-timeout:PT120S}") Duration readTimeout) {
        this.security = security;
        this.compliance = compliance;
        this.tracing = tracing;
        this.objectMapper = objectMapper;
        this.audit = audit;
        this.serviceUrl = serviceUrl.replaceAll("/$", "");
        this.readTimeout = readTimeout;
        this.client = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(connectTimeout).build();
    }

    @PostMapping("/chat")
    public ResponseEntity<String> chat(@RequestBody Map<String, Object> body,
                                       @RequestHeader("Idempotency-Key") String idempotencyKey,
                                       HttpServletRequest http) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        requireChatPermission(principal);
        String traceId = traceId();
        Map<String, Object> secured = secure(body);
        long started = System.currentTimeMillis();
        try {
            HttpResponse<String> response;
            try (TracingService.Span span = tracing.start("chat.upstream")) {
                span.attribute("upstream.path", "/chat").attribute("tenantId", principal.tenantId());
                response = client.send(request("/chat", secured, idempotencyKey, null, false, principal, traceId, http),
                        HttpResponse.BodyHandlers.ofString());
                span.attribute("upstream.status", response.statusCode());
                if (response.statusCode() >= 500) span.markErrorStatus();
            }
            String responseBody = response.body();
            if (response.statusCode() < 400) responseBody = compliance.sanitizeJsonBody(responseBody);
            auditQuery(principal, secured, traceId, response.statusCode() < 400 ? "SUCCESS" : "FAILED",
                    Map.of("upstreamStatus", response.statusCode(), "latencyMs", System.currentTimeMillis() - started));
            return ResponseEntity.status(response.statusCode()).contentType(MediaType.APPLICATION_JSON)
                    .header(TraceContext.TRACE_HEADER, traceId).body(responseBody);
        } catch (java.net.http.HttpTimeoutException ex) {
            auditFailure(principal, secured, traceId, started, ex);
            throw new ApiException(ErrorCodes.SYS_TIMEOUT, HttpStatus.GATEWAY_TIMEOUT, "问答服务响应超时");
        } catch (IOException ex) {
            auditFailure(principal, secured, traceId, started, ex);
            throw new ApiException(ErrorCodes.SYS_DEPENDENCY_UNAVAILABLE, HttpStatus.SERVICE_UNAVAILABLE, "问答服务暂不可用");
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            auditFailure(principal, secured, traceId, started, ex);
            throw new ApiException(ErrorCodes.SYS_DEPENDENCY_UNAVAILABLE, HttpStatus.SERVICE_UNAVAILABLE, "问答调用已中断");
        } catch (RuntimeException ex) {
            auditFailure(principal, secured, traceId, started, ex);
            throw ex;
        }
    }

    @PostMapping(value = "/chat/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public ResponseEntity<StreamingResponseBody> stream(
            @RequestBody Map<String, Object> body,
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @RequestHeader(value = "Last-Event-ID", required = false) String lastEventId,
            HttpServletRequest http) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        requireChatPermission(principal);
        String traceId = traceId();
        Map<String, Object> secured = secure(body);
        long started = System.currentTimeMillis();

        HttpResponse<InputStream> upstream;
        try {
            upstream = client.send(request("/chat/stream", secured, idempotencyKey, lastEventId, true,
                    principal, traceId, http), HttpResponse.BodyHandlers.ofInputStream());
        } catch (java.net.http.HttpTimeoutException ex) {
            auditFailure(principal, secured, traceId, started, ex);
            throw new ApiException(ErrorCodes.SYS_TIMEOUT, HttpStatus.GATEWAY_TIMEOUT, "问答服务响应超时");
        } catch (IOException ex) {
            auditFailure(principal, secured, traceId, started, ex);
            throw new ApiException(ErrorCodes.SYS_DEPENDENCY_UNAVAILABLE, HttpStatus.SERVICE_UNAVAILABLE, "问答服务暂不可用");
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            auditFailure(principal, secured, traceId, started, ex);
            throw new ApiException(ErrorCodes.SYS_DEPENDENCY_UNAVAILABLE, HttpStatus.SERVICE_UNAVAILABLE, "问答调用已中断");
        } catch (RuntimeException ex) {
            auditFailure(principal, secured, traceId, started, ex);
            throw ex;
        }

        if (upstream.statusCode() >= 400) {
            byte[] errorBody;
            try {
                errorBody = upstream.body().readAllBytes();
            } catch (IOException ex) {
                throw new ApiException(ErrorCodes.SYS_DEPENDENCY_UNAVAILABLE, HttpStatus.SERVICE_UNAVAILABLE,
                        "问答服务错误响应读取失败");
            }
            auditQuery(principal, secured, traceId, "FAILED",
                    Map.of("upstreamStatus", upstream.statusCode(), "latencyMs", System.currentTimeMillis() - started));
            StreamingResponseBody payload = output -> output.write(errorBody);
            return ResponseEntity.status(upstream.statusCode()).contentType(MediaType.APPLICATION_JSON)
                    .header(TraceContext.TRACE_HEADER, traceId).body(payload);
        }

        StreamingResponseBody relay = output -> {
            String result = "SUCCESS";
            Map<String, Object> detail = new LinkedHashMap<>();
            detail.put("upstreamStatus", upstream.statusCode());
            String parentSpanId = TraceContext.currentSpanId();
            try (InputStream input = upstream.body();
                 TracingService.Span span = tracing.start("chat.stream.relay", traceId, parentSpanId)) {
                span.attribute("upstream.status", upstream.statusCode());
                boolean blocked = compliance.relay(input, output, traceId);
                detail.put("outputBlocked", blocked);
                if (blocked) span.attribute("security.output.blocked", true);
            } catch (IOException ex) {
                result = "FAILED";
                detail.put("error", ex.getClass().getSimpleName());
                throw ex;
            } finally {
                detail.put("latencyMs", System.currentTimeMillis() - started);
                auditQuery(principal, secured, traceId, result, detail);
            }
        };
        return ResponseEntity.ok().contentType(MediaType.TEXT_EVENT_STREAM)
                .header(HttpHeaders.CACHE_CONTROL, "no-cache").header("X-Accel-Buffering", "no")
                .header(TraceContext.TRACE_HEADER, traceId).body(relay);
    }

    private Map<String, Object> secure(Map<String, Object> original) {
        if (original == null) throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "请求体不能为空");
        Object value = original.get("query");
        if (!(value instanceof String query) || query.isBlank() || query.length() > MAX_QUERY_CHARS) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST,
                    "query 必须为 1 至 " + MAX_QUERY_CHARS + " 个非空白字符");
        }
        Object capability = original.get("capability");
        if (capability != null && !"RAG".equalsIgnoreCase(String.valueOf(capability))) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "P0 仅支持 RAG 能力");
        }
        Object conversationMode = original.get("conversationMode");
        if (conversationMode != null
                && !java.util.Set.of("SINGLE_TURN", "stream", "non-stream").contains(String.valueOf(conversationMode))) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST,
                    "conversationMode 仅支持 SINGLE_TURN / stream / non-stream");
        }
        Map<String, Object> secured = new LinkedHashMap<>(original);
        secured.put("query", security.secureChatQuery(query));
        secured.putIfAbsent("capability", "RAG");
        return secured;
    }

    private HttpRequest request(String path, Map<String, Object> body, String idempotencyKey, String lastEventId,
                                boolean stream, SecurityPrincipal principal, String traceId, HttpServletRequest http)
            throws IOException {
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(serviceUrl + "/api/v1" + path))
                .timeout(readTimeout)
                .header(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
                .header(HttpHeaders.ACCEPT, stream ? MediaType.TEXT_EVENT_STREAM_VALUE : MediaType.APPLICATION_JSON_VALUE)
                .header(TraceContext.REQUEST_ID_HEADER, traceId).header(TraceContext.TRACE_HEADER, traceId)
                .header("X-User-Id", principal.userId()).header("X-Tenant-Id", principal.tenantId())
                .header("X-Roles", String.join(",", principal.roles()))
                .header("X-Permissions", String.join(",", principal.permissions()))
                .POST(HttpRequest.BodyPublishers.ofString(objectMapper.writeValueAsString(body)));
        String traceparent = tracing.currentTraceparent();
        if (traceparent != null) builder.header(TraceContext.TRACEPARENT_HEADER, traceparent);
        if (idempotencyKey != null && !idempotencyKey.isBlank()) builder.header("Idempotency-Key", idempotencyKey);
        String kbIds = kbIdsHeader(principal, http);
        if (!kbIds.isBlank()) builder.header("X-Kb-Ids", kbIds);
        if (lastEventId != null && !lastEventId.isBlank()) builder.header("Last-Event-ID", lastEventId);
        String provider = http == null ? null : http.getHeader("X-LLM-Provider");
        if (provider != null && !provider.isBlank()) builder.header("X-LLM-Provider", provider);
        return builder.build();
    }

    private String kbIdsHeader(SecurityPrincipal principal, HttpServletRequest http) {
        if (principal.permissions().contains("*")) return "";
        Object attribute = http == null ? null : http.getAttribute(GatewayJwtAuthenticationFilter.KB_IDS_ATTRIBUTE);
        if (!(attribute instanceof List<?> values)) return "";
        String joined = String.join(",", values.stream().map(String::valueOf).toList());
        return joined.length() > MAX_KB_ID_HEADER ? joined.substring(0, MAX_KB_ID_HEADER) : joined;
    }

    private void requireChatPermission(SecurityPrincipal principal) {
        if (principal.permissions().contains("*") || principal.permissions().contains("chat:use")) return;
        audit.record(principal, "permission.denied", "permission", "chat:use", "DENIED",
                Map.of("permission", "chat:use"));
        audit.alert("PERMISSION_DENIED", "permission", "chat:use", TraceContext.current(), "permission=chat:use");
        throw new ApiException(ErrorCodes.PERM_FORBIDDEN, HttpStatus.FORBIDDEN, "无权执行该操作");
    }

    private void auditFailure(SecurityPrincipal principal, Map<String, Object> body, String traceId,
                              long started, Exception ex) {
        auditQuery(principal, body, traceId, "FAILED", Map.of("error", ex.getClass().getSimpleName(),
                "latencyMs", System.currentTimeMillis() - started));
    }

    private void auditQuery(SecurityPrincipal principal, Map<String, Object> body, String traceId,
                            String result, Map<String, Object> detail) {
        Map<String, Object> safe = new LinkedHashMap<>(detail);
        safe.put("kbId", body.get("kbId"));
        safe.put("kbIds", body.get("kbIds"));
        safe.put("capability", body.get("capability"));
        safe.put("conversationMode", body.get("conversationMode"));
        safe.put("queryChars", String.valueOf(body.get("query")).length());
        try {
            audit.record(principal, ACTION_CHAT_QUERY, "chat", traceId, result, safe);
        } catch (RuntimeException ex) {
            log.error("chat.query 审计事件写入失败 traceId={}", traceId, ex);
        }
    }

    private static String traceId() {
        String current = TraceContext.current();
        return current == null || current.isBlank() ? "chat-" + UUID.randomUUID() : current;
    }
}
