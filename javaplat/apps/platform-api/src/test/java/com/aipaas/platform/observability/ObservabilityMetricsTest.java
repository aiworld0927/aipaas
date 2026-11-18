package com.aipaas.platform.observability;

import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.util.stream.LongStream;

import static org.junit.jupiter.api.Assertions.*;

/** #177 指标口径：P50/P95/P99 采用最近秩法，成功率与 QPS 按窗口聚合。 */
class ObservabilityMetricsTest {

    private ObservabilityMetrics metrics;

    @BeforeEach
    void setUp() {
        metrics = new ObservabilityMetrics(new SimpleMeterRegistry());
    }

    @Test
    void summaryAggregatesLatencyAndSuccessRate() {
        for (int i = 1; i <= 10; i++) {
            metrics.recordRequest("POST", "/api/v1/chat", i == 10 ? 500 : 200, i * 10L,
                    i == 10 ? "FAILURE" : "SUCCESS");
        }
        Map<String, Object> summary = metrics.summary("15m");
        assertEquals("15m", summary.get("window"));
        assertEquals(50L, summary.get("latencyP50Ms"));
        assertEquals(100L, summary.get("latencyP95Ms"));
        assertEquals(100L, summary.get("latencyP99Ms"));
        assertEquals(0.9, summary.get("successRate"));
        assertEquals(0.1, summary.get("errorRate"));
        assertEquals(0.011, summary.get("qps"));
    }

    @Test
    void percentileUsesNearestRank() {
        List<Long> sorted = LongStream.rangeClosed(1, 100).boxed().toList();
        assertEquals(50L, ObservabilityMetrics.percentile(sorted, 50));
        assertEquals(95L, ObservabilityMetrics.percentile(sorted, 95));
        assertEquals(99L, ObservabilityMetrics.percentile(sorted, 99));
        assertEquals(0L, ObservabilityMetrics.percentile(List.of(), 95));
    }

    @Test
    void emptyWindowIsHealthyAndWindowIsNormalized() {
        Map<String, Object> summary = metrics.summary("bad-window");
        assertEquals("bad-window", summary.get("window"));
        assertEquals(0L, summary.get("latencyP95Ms"));
        assertEquals(1.0, summary.get("successRate"));
        assertEquals(0.0, summary.get("errorRate"));
        assertEquals(0.0, summary.get("qps"));
    }
}
