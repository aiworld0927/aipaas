package com.aipaas.platform.observability;

import com.aipaas.platform.common.web.TraceContext;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.slf4j.MDC;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

/** #175 Trace 数据结构与父 Span 关系、错误标记、按 TraceID 查询。 */
class TracingServiceTest {

    private final TraceStore store = new TraceStore(1000);
    private final TracingService tracing = new TracingService(store, new SimpleMeterRegistry(), "platform-management");

    @AfterEach
    void tearDown() {
        MDC.clear();
    }

    @Test
    void spansShareTraceIdAndKeepParentRelationship() {
        MDC.put(TraceContext.MDC_KEY, "tr_trace001");
        String rootSpan;
        try (TracingService.Span root = tracing.startRoot("POST /api/v1/chat")) {
            root.attribute("http.status_code", 200);
            rootSpan = root.spanId();
            try (TracingService.Span child = tracing.start("chat.upstream")) {
                child.attribute("upstream.status", 200);
            }
        }
        List<TraceSpan> spans = store.byTrace("tr_trace001");
        assertEquals(2, spans.size());
        TraceSpan upstream = spans.stream().filter(s -> s.name().equals("chat.upstream")).findFirst().orElseThrow();
        assertEquals(rootSpan, upstream.parentSpanId());
        assertEquals("LLM", upstream.stage());
        assertTrue(upstream.durationMs() >= 0);
        assertEquals("OK", upstream.status());
    }

    @Test
    void errorMarkingIsPropagatedToStore() {
        MDC.put(TraceContext.MDC_KEY, "tr_trace002");
        try (TracingService.Span span = tracing.start("retrieve.search")) {
            span.markError(new IllegalStateException("backend unavailable"));
        }
        TraceSpan recorded = store.byTrace("tr_trace002").get(0);
        assertEquals("ERROR", recorded.status());
        assertEquals("IllegalStateException", recorded.attributes().get("error.type"));
    }

    @Test
    void traceparentIsW3CCompatible() {
        String traceparent = TraceContext.formatTraceparent("tr_abc", "span1234");
        assertTrue(traceparent.matches("00-[0-9a-f]{32}-[0-9a-f]{16}-01"), traceparent);
        String[] parsed = TraceContext.parseTraceparent(traceparent);
        assertNotNull(parsed);
        assertEquals(traceparent.split("-")[1], parsed[0]);
    }

    @Test
    void pathNormalizationKeepsMetricCardinalityBounded() {
        assertEquals("/api/v1/exports/{id}", ObservabilityMetrics.normalizePath("/api/v1/exports/abc123"));
        assertEquals("/api/v1/traces/{id}",
                ObservabilityMetrics.normalizePath("/api/v1/traces/0123456789abcdef0123456789abcdef"));
        assertEquals("/api/v1/chat", ObservabilityMetrics.normalizePath("/api/v1/chat"));
    }
}
