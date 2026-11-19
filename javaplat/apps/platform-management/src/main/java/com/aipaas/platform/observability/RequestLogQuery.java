package com.aipaas.platform.observability;

import java.time.OffsetDateTime;

public record RequestLogQuery(String tenantId, String traceId, String userId, String path, Integer status,
                              OffsetDateTime from, OffsetDateTime to) {
}
