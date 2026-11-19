package com.aipaas.platform.export;

import java.time.OffsetDateTime;

/** 导出任务快照。 */
public record ExportJob(String id, String tenantId, String kbId, String documentId, String exportType,
                        String format, String status, int progress, String scope, int clauseCount,
                        int retryCount, int maxRetries, String actorId, String idempotencyKey,
                        String objectKey, String fileName, long sizeBytes, String fileHash,
                        String errorCode, String errorMessage, OffsetDateTime expiresAt,
                        OffsetDateTime startedAt, OffsetDateTime finishedAt,
                        OffsetDateTime createdAt, OffsetDateTime updatedAt) {
}
