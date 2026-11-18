package com.aipaas.platform.common.web;

import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.api.ErrorInfo;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.StreamUtils;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingResponseWrapper;

import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 幂等键基础设施：Idempotency-Key + method + path 维度缓存 2xx 响应 24h。
 * 同 key 不同请求体 -> 409 BIZ.CONFLICT（契约语义）。
 * 注：P0 为单实例内存实现；集群化（Redis）属 P1。
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class IdempotencyFilter extends OncePerRequestFilter {

    private static final Set<String> METHODS = Set.of("POST", "PUT", "PATCH", "DELETE");
    private static final long TTL_MILLIS = 24 * 60 * 60 * 1000L;
    private static final String HEADER = "Idempotency-Key";

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final Map<String, Entry> cache = new ConcurrentHashMap<>();

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String contentType = request.getContentType();
        if (contentType != null && contentType.toLowerCase().startsWith("multipart/")) {
            // 大文件由流式上传与 fileHash 幂等保证，避免整包读入内存。
            return true;
        }
        if (request.getRequestURI().matches(".*/documents/uploads/[^/]+/parts/[^/]+$")) {
            // 分片上传天然按 partNo 幂等，且单片 5MB 以内。
            return true;
        }
        return !request.getRequestURI().startsWith("/api/v1")
                || !METHODS.contains(request.getMethod())
                || !StringUtils.hasText(request.getHeader(HEADER));
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        byte[] body = StreamUtils.copyToByteArray(request.getInputStream());
        CachedBodyRequest cachedRequest = new CachedBodyRequest(request, body);
        String key = request.getHeader(HEADER) + "|" + request.getMethod() + "|" + request.getRequestURI();
        int bodyHash = Arrays.hashCode(body);
        Entry existing = cache.get(key);
        if (existing != null && System.currentTimeMillis() - existing.createdAt < TTL_MILLIS) {
            if (existing.bodyHash != bodyHash) {
                writeJson(response, 409, ApiEnvelope.error(ErrorCodes.BIZ_CONFLICT, "幂等键冲突：请求体不一致",
                        ErrorInfo.of("Idempotency-Key reused with a different payload"), TraceContext.current()),
                        TraceContext.current());
                return;
            }
            writeJson(response, existing.status, withCurrentTrace(existing.body), TraceContext.current());
            return;
        }
        ContentCachingResponseWrapper wrappedResponse = new ContentCachingResponseWrapper(response);
        chain.doFilter(cachedRequest, wrappedResponse);
        byte[] responseBody = wrappedResponse.getContentAsByteArray();
        if (wrappedResponse.getStatus() >= 200 && wrappedResponse.getStatus() < 300
                && responseBody.length > 0
                && MediaType.APPLICATION_JSON_VALUE.equals(wrappedResponse.getContentType())) {
            cache.put(key, new Entry(bodyHash, wrappedResponse.getStatus(), responseBody.clone(),
                    System.currentTimeMillis()));
        }
        wrappedResponse.copyBodyToResponse();
    }

    private byte[] withCurrentTrace(byte[] body) {
        try {
            JsonNode node = objectMapper.readTree(body);
            if (node instanceof ObjectNode objectNode) {
                objectNode.put("traceId", TraceContext.current());
            }
            return objectMapper.writeValueAsBytes(node);
        } catch (Exception ignored) {
            return body;
        }
    }

    private void writeJson(HttpServletResponse response, int status, Object payload, String traceId) throws IOException {
        byte[] bytes = objectMapper.writeValueAsBytes(payload);
        response.setStatus(status);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setHeader(TraceContext.TRACE_HEADER, traceId);
        response.setContentLength(bytes.length);
        response.getOutputStream().write(bytes);
    }

    private record Entry(int bodyHash, int status, byte[] body, long createdAt) {
    }

    /** 可重复读取 body 的请求包装，供 bodyHash 与下游共用。 */
    private static final class CachedBodyRequest extends HttpServletRequestWrapper {

        private final byte[] body;

        private CachedBodyRequest(HttpServletRequest request, byte[] body) {
            super(request);
            this.body = body;
        }

        @Override
        public ServletInputStream getInputStream() {
            ByteArrayInputStream input = new ByteArrayInputStream(body);
            return new ServletInputStream() {
                @Override
                public int read() {
                    return input.read();
                }

                @Override
                public boolean isFinished() {
                    return input.available() == 0;
                }

                @Override
                public boolean isReady() {
                    return true;
                }

                @Override
                public void setReadListener(jakarta.servlet.ReadListener readListener) {
                    // no-op
                }
            };
        }

        @Override
        public BufferedReader getReader() {
            return new BufferedReader(new InputStreamReader(getInputStream(), StandardCharsets.UTF_8));
        }

        @Override
        public int getContentLength() {
            return body.length;
        }

        @Override
        public long getContentLengthLong() {
            return body.length;
        }
    }
}
