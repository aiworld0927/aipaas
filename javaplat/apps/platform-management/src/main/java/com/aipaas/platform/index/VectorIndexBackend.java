package com.aipaas.platform.index;

import java.util.Map;

/**
 * #49 索引引擎端口：由 Milvus 适配器实现；P0 默认使用 {@link LoggingVectorIndexBackend}。
 *
 * <p>构建成功后才会调用 {@link #switchAlias}，实现“新集合构建完成 → 别名原子切换”的双集合热切换；
 * 构建失败时不会触发切换，旧索引继续提供服务。</p>
 */
public interface VectorIndexBackend {

    BuildResult build(IndexSpec spec, String collection, int totalChunks, int dimension);

    void switchAlias(String alias, String collection);

    default void drop(String collection) {
        // P0 保留旧集合用于回退，不主动删除
    }

    record BuildResult(int indexedChunks, Map<String, Object> resources) {
    }
}
