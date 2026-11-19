package com.aipaas.platform.observability;

import java.time.OffsetDateTime;

/** 请求日志条目：只含排障元信息，不含请求/响应正文与任何凭证。 */
public record RequestLogEntry(String requestId, String tenantId, String userId, String method, String path,
                              int status, String outcome, int latencyMs, String service, String traceId,
                              String clientIpHash, OffsetDateTime occurredAt) {
}
