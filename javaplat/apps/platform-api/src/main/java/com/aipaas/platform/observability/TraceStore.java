package com.aipaas.platform.observability;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;

/**
 * 有界内存 Span 存储：按 traceId 聚合，容量耗尽时淘汰最旧 Span。
 * P0 单实例足够；多实例场景由 OTel Collector/Jaeger 接管（P1）。
 */
@Component
public class TraceStore {

    private final int maxSpans;
    private final Deque<TraceSpan> spans = new ArrayDeque<>();

    public TraceStore(@Value("${aipaas.observability.trace-max-spans:20000}") int maxSpans) {
        this.maxSpans = Math.max(1000, maxSpans);
    }

    public synchronized void record(TraceSpan span) {
        spans.addLast(span);
        while (spans.size() > maxSpans) {
            spans.removeFirst();
        }
    }

    public synchronized List<TraceSpan> byTrace(String traceId) {
        String wanted = normalize(traceId);
        List<TraceSpan> matched = new ArrayList<>();
        for (TraceSpan span : spans) {
            if (normalize(span.traceId()).equals(wanted)) {
                matched.add(span);
            }
        }
        matched.sort((a, b) -> {
            int byTime = a.startTime().compareTo(b.startTime());
            return byTime != 0 ? byTime : a.spanId().compareTo(b.spanId());
        });
        return matched;
    }

    public synchronized void clear() {
        spans.clear();
    }

    public synchronized int size() {
        return spans.size();
    }

    private static String normalize(String traceId) {
        return traceId == null ? "" : traceId.toLowerCase(Locale.ROOT);
    }
}
