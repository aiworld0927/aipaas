package com.aipaas.platform.audit;

import java.time.OffsetDateTime;

public record AuditQuery(String tenantId, String env, String action, String actorId, String objectType,
                         String objectId, String result, OffsetDateTime from, OffsetDateTime to) {
}
