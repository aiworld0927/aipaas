package com.aipaas.platform.observability;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 从 Python 服务拉取同一 TraceID 的 Span，补齐
 * 解析 → 分块 → 向量化 → 索引 → 检索 → 生成 的全链路视图。
 */
@Component
public class PythonTraceClient {

    private final RestClient queryClient;
    private final RestClient ingestClient;
    private final ObjectMapper mapper;

    public PythonTraceClient(ObjectMapper mapper,
                             @Value("${aipaas.chat.service-url:http://app-query-worker:8002}") String queryUrl,
                             @Value("${aipaas.parse.service-url:http://app-ingest-worker:8000}") String ingestUrl,
                             @Value("${aipaas.observability.trace-source-timeout:PT2S}") Duration timeout) {
        this.mapper = mapper;
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout((int) Math.min(Integer.MAX_VALUE, timeout.toMillis()));
        factory.setReadTimeout((int) Math.min(Integer.MAX_VALUE, timeout.toMillis()));
        this.queryClient = RestClient.builder().baseUrl(trim(queryUrl)).requestFactory(factory).build();
        this.ingestClient = RestClient.builder().baseUrl(trim(ingestUrl)).requestFactory(factory).build();
    }

    public Result fetch(String traceId) {
        List<TraceSpan> spans = new ArrayList<>();
        List<String> unavailable = new ArrayList<>();
        for (RestClient client : List.of(queryClient, ingestClient)) {
            try {
                String body = client.get()
                        .uri("/api/v1/observability/traces/{traceId}", traceId)
                        .retrieve().body(String.class);
                spans.addAll(parse(body));
            } catch (RuntimeException ex) {
                unavailable.add(client.toString());
            }
        }
        return new Result(spans, unavailable);
    }

    private List<TraceSpan> parse(String body) {
        List<TraceSpan> spans = new ArrayList<>();
        if (body == null || body.isBlank()) return spans;
        try {
            JsonNode root = mapper.readTree(body);
            JsonNode items = root.path("data").path("spans");
            if (!items.isArray()) return spans;
            for (JsonNode item : items) {
                String name = text(item, "name");
                spans.add(new TraceSpan(
                        text(item, "traceId"), text(item, "spanId"), text(item, "parentSpanId"),
                        name, item.hasNonNull("stage") ? text(item, "stage") : TraceSpan.stageOf(name),
                        text(item, "service"), text(item, "kind"),
                        instant(text(item, "startTime")), instant(text(item, "endTime")),
                        item.path("durationMs").asDouble(0), text(item, "status"),
                        mapper.convertValue(item.path("attributes"), Map.class),
                        item.hasNonNull("error") ? text(item, "error") : null));
            }
        } catch (Exception ex) {
            // 下游返回不符合契约时忽略该来源，不影响本地 Span 展示。
            return List.of();
        }
        return spans;
    }

    private static String text(JsonNode node, String field) {
        return node.hasNonNull(field) ? node.get(field).asText() : null;
    }

    private static Instant instant(String value) {
        if (value == null || value.isBlank()) return null;
        try {
            return Instant.parse(value);
        } catch (RuntimeException ex) {
            return null;
        }
    }

    private static String trim(String url) {
        return url == null ? "" : url.replaceAll("/$", "");
    }

    public record Result(List<TraceSpan> spans, List<String> unavailableSources) {
    }
}
