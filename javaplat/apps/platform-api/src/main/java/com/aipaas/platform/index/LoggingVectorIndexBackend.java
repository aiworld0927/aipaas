package com.aipaas.platform.index;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.lang.management.ManagementFactory;
import java.util.Map;

/**
 * P0 默认索引引擎：记录 HNSW 构建与别名切换日志，并采集 CPU 资源指标。
 *
 * <p>生产环境由 Milvus 适配器（实现 {@link VectorIndexBackend}）替换；未配置时本实现保证
 * 控制面可运行、状态/耗时/资源可观测，索引内容由 Python {@code HnswIndexService} 负责。</p>
 */
@Component
public class LoggingVectorIndexBackend implements VectorIndexBackend {

    private static final Logger log = LoggerFactory.getLogger(LoggingVectorIndexBackend.class);

    @Override
    public BuildResult build(IndexSpec spec, String collection, int totalChunks, int dimension) {
        log.info("构建 HNSW 索引 collection={} metric={} params={} chunks={} dimension={}",
                collection, spec.metric(), spec.params(), totalChunks, dimension);
        return new BuildResult(totalChunks, Map.of("cpuPercent", cpuPercent(), "source", "jvm"));
    }

    @Override
    public void switchAlias(String alias, String collection) {
        log.info("索引别名切换 alias={} collection={}", alias, collection);
    }

    @Override
    public void drop(String collection) {
        log.info("删除索引集合 collection={}", collection);
    }

    private Double cpuPercent() {
        try {
            java.lang.management.OperatingSystemMXBean bean =
                    ManagementFactory.getOperatingSystemMXBean();
            if (bean instanceof com.sun.management.OperatingSystemMXBean os) {
                double load = os.getCpuLoad();
                return load < 0 ? null : Math.round(load * 10000d) / 100d;
            }
        } catch (Throwable ignored) {
            // 指标不可用时返回 null，不影响构建
        }
        return null;
    }
}
