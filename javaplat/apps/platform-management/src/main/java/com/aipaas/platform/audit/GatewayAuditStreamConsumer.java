package com.aipaas.platform.audit;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.config.SecurityAlertService;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.RedisSystemException;
import org.springframework.data.redis.connection.stream.Consumer;
import org.springframework.data.redis.connection.stream.MapRecord;
import org.springframework.data.redis.connection.stream.ReadOffset;
import org.springframework.data.redis.connection.stream.StreamOffset;
import org.springframework.data.redis.connection.stream.StreamReadOptions;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.Set;

/** 至少一次消费网关审计事件；只有业务处理成功后才 ACK。 */
@Component
public class GatewayAuditStreamConsumer {

    public static final String STREAM_KEY = "aipaas:gateway:audit";
    private static final String GROUP = "platform-management";
    private static final String CONSUMER = "management-1";
    private static final Logger log = LoggerFactory.getLogger(GatewayAuditStreamConsumer.class);

    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;
    private final AuditService audit;
    private final SecurityAlertService alerts;
    private volatile boolean groupReady;

    public GatewayAuditStreamConsumer(StringRedisTemplate redis, ObjectMapper objectMapper,
                                      AuditService audit, SecurityAlertService alerts) {
        this.redis = redis;
        this.objectMapper = objectMapper;
        this.audit = audit;
        this.alerts = alerts;
    }

    @Scheduled(fixedDelayString = "${aipaas.gateway-audit.poll-interval-ms:1000}",
            initialDelayString = "${aipaas.gateway-audit.initial-delay-ms:1000}")
    public void poll() {
        try {
            if (!ensureGroup()) return;
            if (!process(read(ReadOffset.from("0")))) return;
            process(read(ReadOffset.lastConsumed()));
        } catch (RuntimeException ex) {
            log.error("网关审计事件消费失败，未成功记录的事件保持 pending", ex);
        }
    }

    /** @return false 表示 Stream 尚未创建（网关还没写入事件），本轮跳过而不报错。 */
    private boolean ensureGroup() {
        if (groupReady) return true;
        try {
            redis.opsForStream().createGroup(STREAM_KEY, ReadOffset.from("0"), GROUP);
        } catch (RedisSystemException ex) {
            String message = ex.getMessage() == null ? "" : ex.getMessage();
            String cause = ex.getCause() == null || ex.getCause().getMessage() == null
                    ? "" : ex.getCause().getMessage();
            if (message.contains("BUSYGROUP") || cause.contains("BUSYGROUP")) {
                groupReady = true;
                return true;
            }
            if (message.contains("requires the key to exist") || cause.contains("requires the key to exist")) {
                return false;
            }
            throw ex;
        }
        groupReady = true;
        return true;
    }

    private List<MapRecord<String, Object, Object>> read(ReadOffset offset) {
        return redis.opsForStream().read(Consumer.from(GROUP, CONSUMER), StreamReadOptions.empty().count(100),
                StreamOffset.create(STREAM_KEY, offset));
    }

    private boolean process(List<MapRecord<String, Object, Object>> records) {
        if (records == null) return true;
        for (MapRecord<String, Object, Object> record : records) {
            try {
                process(record);
                redis.opsForStream().acknowledge(STREAM_KEY, GROUP, record.getId());
            } catch (Exception ex) {
                log.error("处理网关审计事件失败，recordId={}，保留 pending 等待重试", record.getId(), ex);
                return false;
            }
        }
        return true;
    }

    private void process(MapRecord<String, Object, Object> record) throws Exception {
        Map<Object, Object> value = record.getValue();
        if ("AUDIT".equals(text(value, "eventType"))) {
            Map<String, Object> detail = objectMapper.readValue(text(value, "detail"),
                    new TypeReference<Map<String, Object>>() { });
            SecurityPrincipal actor = new SecurityPrincipal(text(value, "userId"), text(value, "tenantId"),
                    text(value, "username"), text(value, "username"), text(value, "sessionId"), Set.of(), Set.of());
            audit.record(actor, text(value, "action"), text(value, "objectType"), text(value, "objectId"),
                    text(value, "result"), detail, text(value, "ip"), text(value, "userAgent"), text(value, "traceId"));
        } else if ("ALERT".equals(text(value, "eventType"))) {
            alerts.alert(text(value, "alertType"), text(value, "objectType"), text(value, "objectId"),
                    text(value, "traceId"), text(value, "message"));
        } else {
            throw new IllegalArgumentException("Unknown gateway audit event type");
        }
    }

    private static String text(Map<Object, Object> value, String key) {
        Object field = value.get(key);
        return field == null ? "" : String.valueOf(field);
    }
}
