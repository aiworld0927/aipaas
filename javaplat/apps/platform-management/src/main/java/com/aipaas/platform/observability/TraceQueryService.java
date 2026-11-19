package com.aipaas.platform.observability;

import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** 聚合本地与 Python 服务 Span，按 TraceID 回放全链路。 */
@Service
public class TraceQueryService {

    private final TraceStore store;
    private final PythonTraceClient python;

    public TraceQueryService(TraceStore store, PythonTraceClient python) {
        this.store = store;
        this.python = python;
    }

    public Map<String, Object> query(String traceId) {
        Map<String, TraceSpan> merged = new LinkedHashMap<>();
        for (TraceSpan span : store.byTrace(traceId)) {
            merged.putIfAbsent(span.spanId(), span);
        }
        PythonTraceClient.Result remote = python.fetch(traceId);
        for (TraceSpan span : remote.spans()) {
            merged.putIfAbsent(span.spanId(), span);
        }
        List<TraceSpan> spans = new ArrayList<>(merged.values());
        spans.sort(Comparator.comparing(TraceSpan::startTime,
                        Comparator.nullsLast(Comparator.naturalOrder()))
                .thenComparing(TraceSpan::spanId));
        Set<String> services = new LinkedHashSet<>();
        long errors = 0;
        Instant earliest = null;
        Instant latest = null;
        for (TraceSpan span : spans) {
            services.add(span.service());
            if ("ERROR".equals(span.status())) errors++;
            if (span.startTime() != null && (earliest == null || span.startTime().isBefore(earliest))) {
                earliest = span.startTime();
            }
            if (span.endTime() != null && (latest == null || span.endTime().isAfter(latest))) {
                latest = span.endTime();
            }
        }
        long totalLatencyMs = earliest != null && latest != null
                ? Math.max(0, Duration.between(earliest, latest).toMillis()) : 0L;
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("traceId", traceId);
        view.put("totalLatencyMs", totalLatencyMs);
        view.put("status", errors > 0 ? "ERROR" : "OK");
        view.put("spanCount", spans.size());
        view.put("errorSpanCount", errors);
        view.put("services", List.copyOf(services));
        view.put("spans", spans);
        view.put("unavailableSources", remote.unavailableSources());
        view.put("queriedAt", Instant.now().toString());
        return view;
    }
}
