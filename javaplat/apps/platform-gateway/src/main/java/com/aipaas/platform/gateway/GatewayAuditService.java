package com.aipaas.platform.gateway;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.web.TraceContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.data.redis.connection.stream.RecordId;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/** 同步 XADD 可靠审计事件；管理服务成功落库或告警后才 ACK。 */
@Service
public class GatewayAuditService {

    public static final String STREAM_KEY = "aipaas:gateway:audit";
    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;

    public GatewayAuditService(StringRedisTemplate redis, ObjectMapper objectMapper) {
        this.redis = redis;
        this.objectMapper = objectMapper;
    }

    public void record(SecurityPrincipal actor, String action, String objectType, String objectId,
                       String result, Map<String, Object> detail) {
        try {
            Map<String, String> event = base("AUDIT", objectType, objectId, TraceContext.current());
            event.put("tenantId", safe(actor.tenantId()));
            event.put("userId", safe(actor.userId()));
            event.put("username", safe(actor.username()));
            event.put("sessionId", safe(actor.sessionId()));
            event.put("action", safe(action));
            event.put("result", safe(result));
            event.put("detail", objectMapper.writeValueAsString(detail == null ? Map.of() : detail));
            event.put("ip", "");
            event.put("userAgent", "");
            append(event);
        } catch (com.fasterxml.jackson.core.JsonProcessingException ex) {
            throw new IllegalStateException("Unable to serialize gateway audit detail", ex);
        }
    }

    public void alert(String type, String objectType, String objectId, String traceId, String safeSummary) {
        Map<String, String> event = base("ALERT", objectType, objectId, traceId);
        event.put("alertType", safe(type));
        event.put("message", safe(safeSummary));
        append(event);
    }

    private Map<String, String> base(String type, String objectType, String objectId, String traceId) {
        Map<String, String> event = new LinkedHashMap<>();
        event.put("eventType", type);
        event.put("eventId", UUID.randomUUID().toString());
        event.put("objectType", safe(objectType));
        event.put("objectId", safe(objectId));
        event.put("traceId", safe(traceId));
        return event;
    }

    private void append(Map<String, String> event) {
        RecordId id = redis.opsForStream().add(STREAM_KEY, event);
        if (id == null) throw new IllegalStateException("Redis did not acknowledge audit stream append");
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }
}
