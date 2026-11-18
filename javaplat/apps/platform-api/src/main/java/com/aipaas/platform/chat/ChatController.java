package com.aipaas.platform.chat;

import com.aipaas.platform.audit.AuditService;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.auth.UserRepository;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.content.ContentSecurityService;
import com.aipaas.platform.content.OutputComplianceGuard;
import com.aipaas.platform.observability.TracingService;
import com.aipaas.platform.rbac.RequirePermission;
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

/**
 * #407 问答编排入口（Java 统一对外）：
 * 输入校验 → 内容安全检查 → 身份/权限/知识库授权头注入 → 内部 Python 问答服务
 * → 引用与拒答结果回传；#113 SSE 流式在同一入口透传，并支持 Last-Event-ID 续传。
 *
 * 编排层只做跨语言编排与安全边界，不实现检索/生成算法；TraceID 由网关注入，
 * 同时写入 X-Request-Id 与 X-Trace-Id，串联检索与生成各环节日志与审计。
 */
@RestController
@RequestMapping("/api/v1")
public class ChatController {

    private static final Logger log = LoggerFactory.getLogger(ChatController.class);
    private static final String ACTION_CHAT_QUERY = "chat.query";
    private static final int MAX_QUERY_CHARS = 2000;
    private static final int MAX_KB_ID_HEADER = 4096;

    private final ContentSecurityService security;
    private final OutputComplianceGuard compliance;
    private final TracingService tracing;
    private final ObjectMapper objectMapper;
    private final AuditService audit;
    private final UserRepository users;
    private final HttpClient client;
    private final String serviceUrl;
    private final Duration readTimeout;

    public ChatController(ContentSecurityService security, OutputComplianceGuard compliance, TracingService tracing,
                          ObjectMapper objectMapper, AuditService audit,
                          UserRepository users,
                          @Value("${aipaas.chat.service-url:http://svc-query:8002}") String serviceUrl,
                          @Value("${aipaas.chat.connect-timeout:PT5S}") Duration connectTimeout,
                          @Value("${aipaas.chat.read-timeout:PT120S}") Duration readTimeout) {
        this.security = security;
        this.compliance = compliance;
        this.tracing = tracing;
        this.objectMapper = objectMapper;
        this.audit = audit;
        this.users = users;
        this.serviceUrl = serviceUrl.replaceAll("/$", "");
        this.readTimeout = readTimeout;
        this.client = HttpClient.newBuilder().connectTimeout(connectTimeout).build();
    }

    /** 非流式问答：P0 单轮；sessionId/conversationMode 透传但不保持上下文（P1 #115）。 */
    @PostMapping("/chat")
    @RequirePermission("chat:use")
    public ResponseEntity<String> chat(@RequestBody Map<String, Object> body,
                                       @RequestHeader("Idempotency-Key") String idempotencyKey,
                                       HttpServletRequest http) throws IOException, InterruptedException {
        SecurityPrincipal principal = SecurityPrincipal.current();
        String traceId = traceId();
        Map<String, Object> secured = secure(body);
        long started = System.currentTimeMillis();
        try {
            HttpResponse<String> response;
            try (TracingService.Span span = tracing.start("chat.upstream")) {
                span.attribute("upstream.path", "/chat").attribute("tenantId", principal.tenantId());
                response = client.send(
                        request("/chat", secured, idempotencyKey, null, false, principal, traceId, http),
                        HttpResponse.BodyHandlers.ofString());
                span.attribute("upstream.status", response.statusCode());
                if (response.statusCode() >= 500) {
                    span.markErrorStatus();
                }
            }
            String responseBody = response.body();
            if (response.statusCode() < 400) {
                // #157 输出合规：命中 BLOCK 直接 422，命中 REPLACE 原位替换后再回传。
                responseBody = compliance.sanitizeJsonBody(responseBody);
            }
            auditQuery(principal, secured, traceId,
                    response.statusCode() < 400 ? "SUCCESS" : "FAILED",
                    Map.of("upstreamStatus", response.statusCode(),
                            "latencyMs", System.currentTimeMillis() - started));
            return ResponseEntity.status(response.statusCode())
                    .contentType(MediaType.APPLICATION_JSON)
                    .header(TraceContext.TRACE_HEADER, traceId)
                    .body(responseBody);
        } catch (IOException | InterruptedException | RuntimeException ex) {
            auditQuery(principal, secured, traceId, "FAILED",
                    Map.of("error", ex.getClass().getSimpleName(),
                            "latencyMs", System.currentTimeMillis() - started));
            throw ex;
        }
    }

    /**
     * SSE 流式问答：事件 meta / citation / delta / done / error 原样透传，
     * 并透传 Last-Event-ID 以支持断线续传（P0 单实例内存缓冲）。
     */
    @PostMapping(value = "/chat/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    @RequirePermission("chat:use")
    public ResponseEntity<StreamingResponseBody> stream(
            @RequestBody Map<String, Object> body,
            @RequestHeader("Idempotency-Key") String idempotencyKey,
            @RequestHeader(value = "Last-Event-ID", required = false) String lastEventId,
            HttpServletRequest http) throws IOException, InterruptedException {
        SecurityPrincipal principal = SecurityPrincipal.current();
        String traceId = traceId();
        Map<String, Object> secured = secure(body);
        long started = System.currentTimeMillis();

        HttpResponse<InputStream> upstream;
        try {
            upstream = client.send(
                    request("/chat/stream", secured, idempotencyKey, lastEventId, true, principal, traceId, http),
                    HttpResponse.BodyHandlers.ofInputStream());
        } catch (IOException | InterruptedException | RuntimeException ex) {
            auditQuery(principal, secured, traceId, "FAILED",
                    Map.of("error", ex.getClass().getSimpleName(),
                            "latencyMs", System.currentTimeMillis() - started));
            throw ex;
        }

        if (upstream.statusCode() >= 400) {
            byte[] errorBody = upstream.body().readAllBytes();
            auditQuery(principal, secured, traceId, "FAILED",
                    Map.of("upstreamStatus", upstream.statusCode(),
                            "latencyMs", System.currentTimeMillis() - started));
            StreamingResponseBody payload = output -> output.write(errorBody);
            return ResponseEntity.status(upstream.statusCode())
                    .contentType(MediaType.APPLICATION_JSON)
                    .header(TraceContext.TRACE_HEADER, traceId)
                    .body(payload);
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
                if (blocked) {
                    span.attribute("security.output.blocked", true);
                }
            } catch (IOException ex) {
                result = "FAILED";
                detail.put("error", ex.getClass().getSimpleName());
                throw ex;
            } finally {
                detail.put("latencyMs", System.currentTimeMillis() - started);
                // 异步写出线程没有 SecurityContext，使用捕获的主体写审计。
                auditQuery(principal, secured, traceId, result, detail);
            }
        };
        return ResponseEntity.ok()
                .contentType(MediaType.TEXT_EVENT_STREAM)
                .header(HttpHeaders.CACHE_CONTROL, "no-cache")
                .header("X-Accel-Buffering", "no")
                .header(TraceContext.TRACE_HEADER, traceId)
                .body(relay);
    }

    /** 入口校验 + 安全检查；两者都在构造下游请求之前完成。 */
    private Map<String, Object> secure(Map<String, Object> original) {
        if (original == null) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "请求体不能为空");
        }
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
                .header(HttpHeaders.ACCEPT,
                        stream ? MediaType.TEXT_EVENT_STREAM_VALUE : MediaType.APPLICATION_JSON_VALUE)
                .header(TraceContext.REQUEST_ID_HEADER, traceId)
                .header(TraceContext.TRACE_HEADER, traceId)
                .header("X-User-Id", principal.userId())
                .header("X-Tenant-Id", principal.tenantId())
                .header("X-Roles", String.join(",", principal.roles()))
                .header("X-Permissions", String.join(",", principal.permissions()))
                .POST(HttpRequest.BodyPublishers.ofString(objectMapper.writeValueAsString(body)));
        String traceparent = tracing.currentTraceparent();
        if (traceparent != null) {
            builder.header(TraceContext.TRACEPARENT_HEADER, traceparent);
        }
        if (idempotencyKey != null && !idempotencyKey.isBlank()) {
            builder.header("Idempotency-Key", idempotencyKey);
        }
        String kbIds = kbIdsHeader(principal);
        if (!kbIds.isBlank()) {
            builder.header("X-Kb-Ids", kbIds);
        }
        if (lastEventId != null && !lastEventId.isBlank()) {
            builder.header("Last-Event-ID", lastEventId);
        }
        String provider = http == null ? null : http.getHeader("X-LLM-Provider");
        if (provider != null && !provider.isBlank()) {
            builder.header("X-LLM-Provider", provider);
        }
        return builder.build();
    }

    /** 授权知识库：拥有 * 权限时不展开（Python 侧按 all_kbs 处理）。 */
    private String kbIdsHeader(SecurityPrincipal principal) {
        if (principal.permissions().contains("*")) {
            return "";
        }
        List<String> kbIds = users.permittedKbIds(principal.userId(), "retrieval:query");
        String joined = String.join(",", kbIds);
        return joined.length() > MAX_KB_ID_HEADER ? joined.substring(0, MAX_KB_ID_HEADER) : joined;
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
            // 审计失败不改变问答结果，但要能在日志中定位。
            log.error("chat.query 审计写入失败 traceId={}", traceId, ex);
        }
    }

    private static String traceId() {
        String current = TraceContext.current();
        return current == null || current.isBlank() ? "chat-" + UUID.randomUUID() : current;
    }
}
