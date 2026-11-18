package com.aipaas.platform.audit;

import java.time.OffsetDateTime;
import java.util.Map;

public record AuditEntry(long sequenceNo, String id, String tenantId, String actorId, String actorName,
                         String action, String objectType, String objectId, String result, String ip,
                         String userAgent, String traceId, String detailJson, String previousHash,
                         String entryHash, String env, boolean archive, OffsetDateTime createdAt) {

    public Map<String, Object> toResponse(Object details) {
        java.util.LinkedHashMap<String, Object> value = new java.util.LinkedHashMap<>();
        value.put("id", id);
        value.put("tenantId", tenantId);
        value.put("actorId", actorId);
        value.put("actorName", actorName);
        value.put("action", action);
        value.put("objectType", objectType);
        value.put("objectId", objectId);
        value.put("result", result);
        value.put("ip", ip);
        value.put("userAgent", userAgent);
        value.put("traceId", traceId);
        value.put("detail", details);
        value.put("createdAt", createdAt);
        return value;
    }
}
