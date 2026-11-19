package com.aipaas.platform.observability;

import com.aipaas.platform.common.web.TraceContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * #173 结构化请求日志：所有业务请求（成功/失败）都记录
 * 时间、用户、接口、耗时、状态码与 TraceID；不记录查询串、
 * 请求体、响应体、Authorization/Cookie 等敏感内容。
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 20)
public class RequestLogFilter extends OncePerRequestFilter {

    public static final String REQUEST_LOG = "REQUEST_LOG";
    private static final Logger structured = LoggerFactory.getLogger(REQUEST_LOG);
    private static final int MAX_PATH = 512;

    private final RequestLogService service;
    private final ObservabilityMetrics metrics;
    private final ObjectMapper mapper;
    private final String serviceName;

    public RequestLogFilter(RequestLogService service, ObservabilityMetrics metrics, ObjectMapper mapper,
                            @Value("${aipaas.observability.service-name:platform-management}") String serviceName) {
        this.service = service;
        this.metrics = metrics;
        this.mapper = mapper;
        this.serviceName = serviceName;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return isInfrastructure(request.getRequestURI());
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        long started = System.nanoTime();
        try {
            chain.doFilter(request, response);
        } finally {
            int status = response.getStatus();
            long durationMs = Math.max(0, (System.nanoTime() - started) / 1_000_000);
            String outcome = status < 400 ? "SUCCESS" : status == 401 || status == 403 ? "DENIED" : "FAILURE";
            String path = truncate(request.getRequestURI(), MAX_PATH);
            String traceId = TraceContext.current();
            String userId = attribute(request, "security.userId");
            String tenantId = attribute(request, "security.tenantId");
            if (tenantId.isBlank()) tenantId = "default";
            String ipHash = hash(request.getRemoteAddr());
            String method = request.getMethod();

            RequestLogEntry entry = new RequestLogEntry(UUID.randomUUID().toString().replace("-", ""), tenantId,
                    userId, method, path, status, outcome, (int) Math.min(Integer.MAX_VALUE, durationMs),
                    serviceName, traceId, ipHash, OffsetDateTime.now(ZoneOffset.UTC));
            service.record(entry);
            metrics.recordRequest(method, path, status, durationMs, outcome);
            writeStructured(entry);
        }
    }

    private void writeStructured(RequestLogEntry entry) {
        Map<String, Object> line = new LinkedHashMap<>();
        line.put("ts", entry.occurredAt().toString());
        line.put("level", entry.status() >= 500 ? "ERROR" : "INFO");
        line.put("kind", "request");
        line.put("service", entry.service());
        line.put("traceId", entry.traceId());
        line.put("userId", entry.userId());
        line.put("tenantId", entry.tenantId());
        line.put("method", entry.method());
        line.put("path", entry.path());
        line.put("status", entry.status());
        line.put("durationMs", entry.latencyMs());
        line.put("outcome", entry.outcome());
        line.put("clientIpHash", entry.clientIpHash());
        try {
            structured.info(mapper.writeValueAsString(line));
        } catch (IOException ex) {
            structured.info("request traceId={} method={} path={} status={} durationMs={} outcome={}",
                    entry.traceId(), entry.method(), entry.path(), entry.status(), entry.latencyMs(),
                    entry.outcome());
        }
    }

    private static String attribute(HttpServletRequest request, String name) {
        Object value = request.getAttribute(name);
        return value == null ? "" : String.valueOf(value);
    }

    private static String truncate(String value, int max) {
        if (value == null) return "";
        return value.length() <= max ? value : value.substring(0, max);
    }

    private static String hash(String value) {
        if (value == null || value.isBlank()) return "";
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest).substring(0, 32);
        } catch (NoSuchAlgorithmException ex) {
            return "";
        }
    }

    private static boolean isInfrastructure(String path) {
        return path == null || path.startsWith("/actuator") || path.equals("/healthz") || path.equals("/readyz")
                || path.equals("/metrics") || path.startsWith("/error");
    }
}
