package com.aipaas.platform.common.web;

import org.slf4j.MDC;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/** 链路上下文：请求头 X-Request-Id / W3C traceparent / 响应头 X-Trace-Id / 信封 traceId 四处一致。 */
public final class TraceContext {

    public static final String TRACE_HEADER = "X-Trace-Id";
    public static final String REQUEST_ID_HEADER = "X-Request-Id";
    public static final String TRACEPARENT_HEADER = "traceparent";
    public static final String REQUEST_ATTR = "aipaas.traceId";
    public static final String MDC_KEY = "traceId";
    public static final String MDC_SPAN_KEY = "spanId";

    private static final String ZERO_TRACE = "00000000000000000000000000000000";
    private static final String ZERO_SPAN = "0000000000000000";

    private TraceContext() {
    }

    public static String current() {
        String value = MDC.get(MDC_KEY);
        return value == null ? "" : value;
    }

    public static String currentSpanId() {
        String value = MDC.get(MDC_SPAN_KEY);
        return value == null ? "" : value;
    }

    /** 把任意 TraceID 归一为 32 位小写十六进制，满足 W3C traceparent 约束。 */
    public static String otelTraceId(String traceId) {
        if (traceId != null && traceId.matches("[0-9a-f]{32}") && !ZERO_TRACE.equals(traceId)) {
            return traceId;
        }
        String seed = traceId == null ? "" : traceId;
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(seed.getBytes(StandardCharsets.UTF_8));
            String hex = HexFormat.of().formatHex(digest);
            String candidate = hex.substring(0, 32);
            return ZERO_TRACE.equals(candidate) ? "0".repeat(31) + "1" : candidate;
        } catch (NoSuchAlgorithmException ex) {
            return "1".repeat(32);
        }
    }

    public static String otelSpanId(String spanId) {
        if (spanId != null && spanId.matches("[0-9a-f]{16}") && !ZERO_SPAN.equals(spanId)) {
            return spanId;
        }
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest((spanId == null ? "" : spanId).getBytes(StandardCharsets.UTF_8));
            String candidate = HexFormat.of().formatHex(digest).substring(0, 16);
            return ZERO_SPAN.equals(candidate) ? "0".repeat(15) + "1" : candidate;
        } catch (NoSuchAlgorithmException ex) {
            return "1".repeat(16);
        }
    }

    /** 解析 W3C traceparent，返回 {traceId, parentSpanId, flags}；非法返回 null。 */
    public static String[] parseTraceparent(String header) {
        if (header == null || header.isBlank()) return null;
        String[] parts = header.trim().split("-");
        if (parts.length != 4 || parts[0].length() != 2 || parts[1].length() != 32 || parts[2].length() != 16) {
            return null;
        }
        String traceId = parts[1].toLowerCase();
        String spanId = parts[2].toLowerCase();
        if (!traceId.matches("[0-9a-f]{32}") || !spanId.matches("[0-9a-f]{16}")
                || ZERO_TRACE.equals(traceId) || ZERO_SPAN.equals(spanId)) {
            return null;
        }
        return new String[]{traceId, spanId, parts[3] == null || parts[3].isBlank() ? "01" : parts[3]};
    }

    public static String formatTraceparent(String traceId, String spanId) {
        return "00-" + otelTraceId(traceId) + "-" + otelSpanId(spanId) + "-01";
    }
}
