package com.aipaas.platform.observability;

import com.aipaas.platform.common.web.TraceContext;
import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.MDC;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * #175 全链路 Trace：生成/延续 W3C TraceID，记录各环节 Span 耗时与错误标记。
 *
 * Span 同时写入有界内存（供 /api/v1/traces/{traceId} 回放）和 Prometheus
 * ``aipaas_span_duration_seconds``；不记录任何业务正文。
 */
@Service
public class TracingService {

    private final TraceStore store;
    private final MeterRegistry meters;
    private final String serviceName;

    public TracingService(TraceStore store, MeterRegistry meters,
                          @Value("${aipaas.observability.service-name:aipaas}") String serviceName) {
        this.store = store;
        this.meters = meters;
        this.serviceName = serviceName;
    }

    public Span startRoot(String name) {
        return new Span(name, TraceContext.current(), TraceContext.currentSpanId(), "SERVER");
    }

    public Span start(String name) {
        return new Span(name, TraceContext.current(), TraceContext.currentSpanId(), "INTERNAL");
    }

    /** 异步线程（如 SSE 写出）没有 MDC，需要显式传入 Trace 上下文。 */
    public Span start(String name, String traceId, String parentSpanId) {
        return new Span(name, traceId, parentSpanId, "INTERNAL");
    }

    public String currentTraceparent() {
        String traceId = TraceContext.current();
        String spanId = TraceContext.currentSpanId();
        return traceId == null || traceId.isBlank() ? null : TraceContext.formatTraceparent(traceId, spanId);
    }

    public final class Span implements AutoCloseable {

        private final String name;
        private final String traceId;
        private final String spanId;
        private final String parentSpanId;
        private final String kind;
        private final Instant startTime = Instant.now();
        private final long startNanos = System.nanoTime();
        private final String previousTrace;
        private final String previousSpan;
        private final Map<String, Object> attributes = new LinkedHashMap<>();
        private String status = "OK";
        private String error;
        private boolean ended;

        private Span(String name, String traceId, String parentSpanId, String kind) {
            this.name = name;
            this.traceId = traceId == null ? "" : traceId;
            this.parentSpanId = parentSpanId == null || parentSpanId.isBlank() ? null : parentSpanId;
            this.spanId = newSpanId();
            this.kind = kind;
            this.previousTrace = MDC.get(TraceContext.MDC_KEY);
            this.previousSpan = MDC.get(TraceContext.MDC_SPAN_KEY);
            MDC.put(TraceContext.MDC_KEY, this.traceId);
            MDC.put(TraceContext.MDC_SPAN_KEY, this.spanId);
        }

        public Span attribute(String key, Object value) {
            if (key != null && value != null) {
                attributes.put(key, value);
            }
            return this;
        }

        public Span markError(Throwable throwable) {
            this.status = "ERROR";
            if (throwable != null) {
                String message = throwable.getClass().getSimpleName() + ": " + throwable.getMessage();
                this.error = message.substring(0, Math.min(256, message.length()));
                attributes.put("error.type", throwable.getClass().getSimpleName());
            }
            return this;
        }

        public Span markErrorStatus() {
            this.status = "ERROR";
            return this;
        }

        public String traceId() {
            return traceId;
        }

        public String spanId() {
            return spanId;
        }

        @Override
        public void close() {
            if (ended) return;
            ended = true;
            long elapsedNanos = System.nanoTime() - startNanos;
            double durationMs = elapsedNanos / 1_000_000.0;
            store.record(new TraceSpan(traceId, spanId, parentSpanId, name, TraceSpan.stageOf(name), serviceName,
                    kind, startTime, Instant.now(), round(durationMs), status, Map.copyOf(attributes), error));
            meters.timer("aipaas.span.duration", "service", serviceName, "name", name, "status", status)
                    .record(Duration.ofNanos(elapsedNanos));
            restore(TraceContext.MDC_SPAN_KEY, previousSpan);
            restore(TraceContext.MDC_KEY, previousTrace);
        }
    }

    private static void restore(String key, String value) {
        if (value == null) {
            MDC.remove(key);
        } else {
            MDC.put(key, value);
        }
    }

    private static String newSpanId() {
        return UUID.randomUUID().toString().replace("-", "").substring(0, 16);
    }

    private static double round(double value) {
        return Math.round(value * 1000.0) / 1000.0;
    }
}
