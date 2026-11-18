package com.aipaas.platform.observability;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.stereotype.Component;

import java.lang.management.ManagementFactory;
import java.time.Duration;
import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * #177 业务指标：请求 QPS/延迟/成功率、安全拦截、检索命中与拒答由 Python 侧上报。
 *
 * 延迟口径：P50/P95/P99 由 Micrometer 百分位直方图计算，与 NFR 定义一致；
 * Prometheus 侧可用 histogram_quantile(0.95, ...) 复核。
 * 另外保留最近 10000 条请求样本，支撑 /observability/metrics/summary 的窗口聚合。
 */
@Component
public class ObservabilityMetrics {

    private static final int MAX_SAMPLES = 10_000;

    private final MeterRegistry meters;
    private final Deque<Sample> samples = new ArrayDeque<>();

    public ObservabilityMetrics(MeterRegistry meters) {
        this.meters = meters;
    }

    public void recordRequest(String method, String path, int status, long durationMs, String outcome) {
        String normalized = normalizePath(path);
        Counter.builder("aipaas.requests.total")
                .description("HTTP requests by method/path/status")
                .tags("service", "platform-api", "method", method, "path", normalized,
                        "status", String.valueOf(status), "outcome", outcome)
                .register(meters)
                .increment();
        Timer.builder("aipaas.request.duration")
                .description("HTTP request latency (P50/P95/P99)")
                .publishPercentiles(0.5, 0.95, 0.99)
                .publishPercentileHistogram()
                .tags("service", "platform-api", "method", method, "path", normalized)
                .register(meters)
                .record(Duration.ofMillis(Math.max(0, durationMs)));
        synchronized (samples) {
            samples.addLast(new Sample(System.currentTimeMillis(), Math.max(0, durationMs), status < 400));
            while (samples.size() > MAX_SAMPLES) {
                samples.removeFirst();
            }
        }
    }

    /** 契约 /observability/metrics/summary：窗口内 QPS、P50/P95/P99、成功率与资源占用。 */
    public Map<String, Object> summary(String window) {
        long windowMs = parseWindow(window);
        long cutoff = System.currentTimeMillis() - windowMs;
        List<Long> latencies = new ArrayList<>();
        long total = 0;
        long success = 0;
        synchronized (samples) {
            for (Sample sample : samples) {
                if (sample.epochMillis() < cutoff) continue;
                total++;
                latencies.add(sample.latencyMs());
                if (sample.success()) success++;
            }
        }
        latencies.sort(Long::compareTo);
        double successRate = total == 0 ? 1.0 : (double) success / total;
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("window", window == null ? "15m" : window);
        data.put("qps", Math.round(total / (windowMs / 1000.0) * 1000.0) / 1000.0);
        data.put("latencyP50Ms", percentile(latencies, 50));
        data.put("latencyP95Ms", percentile(latencies, 95));
        data.put("latencyP99Ms", percentile(latencies, 99));
        data.put("errorRate", total == 0 ? 0.0 : Math.round((1 - successRate) * 10000.0) / 10000.0);
        data.put("successRate", Math.round(successRate * 10000.0) / 10000.0);
        data.put("cpuUsage", processCpuUsage());
        data.put("memoryUsage", heapUsage());
        data.put("collectedAt", java.time.Instant.now().toString());
        return data;
    }

    public void recordSecurityCheck(String type, String action, boolean passed) {
        Counter.builder("aipaas.security.checks.total")
                .description("Content security checks")
                .tags("type", type, "action", action, "result", passed ? "PASSED" : "BLOCKED")
                .register(meters)
                .increment();
        if (!passed) {
            Counter.builder("aipaas.security.blocks.total")
                    .description("Content security blocks")
                    .tags("type", type)
                    .register(meters)
                    .increment();
        }
    }

    static long parseWindow(String window) {
        if (window == null) return Duration.ofMinutes(15).toMillis();
        return switch (window) {
            case "5m" -> Duration.ofMinutes(5).toMillis();
            case "1h" -> Duration.ofHours(1).toMillis();
            case "24h" -> Duration.ofHours(24).toMillis();
            default -> Duration.ofMinutes(15).toMillis();
        };
    }

    /** 最近秩法（nearest-rank）：P95 = 排序后第 ceil(0.95*n) 个样本，与 NFR 口径一致。 */
    static long percentile(List<Long> sorted, double percent) {
        if (sorted.isEmpty()) return 0L;
        int rank = (int) Math.ceil(percent / 100.0 * sorted.size());
        int index = Math.min(sorted.size() - 1, Math.max(0, rank - 1));
        return sorted.get(index);
    }

    private static double processCpuUsage() {
        try {
            var os = ManagementFactory.getOperatingSystemMXBean();
            if (os instanceof com.sun.management.OperatingSystemMXBean sun) {
                double load = sun.getProcessCpuLoad();
                if (load < 0) load = sun.getCpuLoad();
                if (load >= 0) return Math.round(load * 10000.0) / 10000.0;
            }
        } catch (RuntimeException ignored) {
            // 受限容器读不到 CPU 指标时返回 0，不影响接口可用性。
        }
        return 0.0;
    }

    private static double heapUsage() {
        Runtime runtime = Runtime.getRuntime();
        long max = runtime.maxMemory();
        if (max <= 0) return 0.0;
        double used = (double) (runtime.totalMemory() - runtime.freeMemory()) / max;
        return Math.round(used * 10000.0) / 10000.0;
    }

    /** 降基数：UUID/长十六进制/纯数字路径段归一为 {id}。 */
    static String normalizePath(String path) {
        if (path == null || path.isBlank()) return "/";
        String[] segments = path.split("/");
        StringBuilder builder = new StringBuilder();
        for (String segment : segments) {
            if (segment.isEmpty()) continue;
            builder.append('/');
            String lower = segment.toLowerCase(Locale.ROOT);
            if (lower.matches("[0-9a-f]{16,}") || lower.matches("[0-9a-f-]{32,}")
                    || segment.matches("\\d+")
                    || (segment.matches("[A-Za-z0-9_-]{6,}") && segment.matches(".*\\d.*"))) {
                builder.append("{id}");
            } else {
                builder.append(segment);
            }
        }
        return builder.isEmpty() ? "/" : builder.toString();
    }

    private record Sample(long epochMillis, long latencyMs, boolean success) {
    }
}
