package com.aipaas.platform.observability;

import java.time.Instant;
import java.util.Locale;
import java.util.Map;

/** OpenTelemetry 数据模型的 Span 快照；只保存元信息，不保存请求/响应内容。 */
public record TraceSpan(String traceId, String spanId, String parentSpanId, String name, String stage,
                        String service, String kind, Instant startTime, Instant endTime, double durationMs,
                        String status, Map<String, Object> attributes, String error) {

    /** 契约字段 stage：由 Span 名称推导，便于看板按环节聚合。 */
    public static String stageOf(String name) {
        String value = name == null ? "" : name.toLowerCase(Locale.ROOT);
        if (value.contains("security")) return "SECURITY";
        if (value.startsWith("auth") || value.startsWith("login")) return "AUTH";
        if (value.startsWith("rerank")) return "RERANK";
        if (value.startsWith("retrieve") || value.startsWith("search")) return "RETRIEVE";
        if (value.startsWith("parse") || value.startsWith("chunk") || value.startsWith("index")
                || value.startsWith("embed")) return "INDEX";
        if (value.startsWith("prompt")) return "PROMPT";
        if (value.startsWith("chat") || value.startsWith("generate") || value.startsWith("llm")) return "LLM";
        return "OTHER";
    }
}
