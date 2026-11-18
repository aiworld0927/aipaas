package com.aipaas.platform.index;

import java.time.OffsetDateTime;

/** vector_index_builds 行模型。 */
public record IndexBuildRow(
        String id,
        String tenantId,
        String kbId,
        String idempotencyKey,
        String status,
        int progress,
        IndexSpec spec,
        String resolvedSlot,
        String collection,
        String previousCollection,
        int versionNum,
        int dimension,
        int totalChunks,
        int indexedChunks,
        Long durationMs,
        String resourceJson,
        String documentIdsJson,
        int attempt,
        boolean rolledBack,
        String errorMessage,
        OffsetDateTime startedAt,
        OffsetDateTime finishedAt,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt) {
}
