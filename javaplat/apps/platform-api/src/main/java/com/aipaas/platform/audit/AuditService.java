package com.aipaas.platform.audit;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.crypto.KeyringService;
import com.aipaas.platform.common.web.TraceContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;

@Service
public class AuditService {

    static final String GENESIS = "0".repeat(64);
    private final AuditRepository repository;
    private final KeyringService keyring;
    private final ObjectMapper objectMapper;
    private final String env;

    public AuditService(AuditRepository repository, KeyringService keyring, ObjectMapper objectMapper,
                        @Value("${aipaas.env:prod}") String env) {
        this.repository = repository;
        this.keyring = keyring;
        this.objectMapper = objectMapper;
        this.env = env;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public synchronized AuditEntry record(SecurityPrincipal actor, String action, String objectType, String objectId,
                                          String result, Map<String, Object> detail, String ip, String userAgent,
                                          String traceId) {
        try {
            String safeDetail = objectMapper.writeValueAsString(sanitize(detail));
            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC).truncatedTo(java.time.temporal.ChronoUnit.MICROS);
            String previous = repository.latest().map(AuditEntry::entryHash).orElse(GENESIS);
            String id = "aud_" + UUID.randomUUID().toString().replace("-", "");
            String safeIp = abbreviate(ip, 64);
            String safeUserAgent = abbreviate(userAgent, 512);
            String safeTraceId = traceId == null ? "" : traceId;
            String entryHash = hash(canonical(id, actor.tenantId(), actor.userId(), actor.username(), action, objectType,
                    objectId, result, safeIp, safeUserAgent, safeTraceId, safeDetail, previous, env, now));
            AuditEntry entry = new AuditEntry(0, id, actor.tenantId(), actor.userId(), actor.username(), action,
                    objectType, objectId, result, safeIp, safeUserAgent, safeTraceId, safeDetail,
                    previous, entryHash, env, false, now);
            repository.insert(entry);
            return entry;
        } catch (GeneralSecurityException | com.fasterxml.jackson.core.JsonProcessingException ex) {
            throw new IllegalStateException("Unable to append audit log", ex);
        }
    }

    public void record(SecurityPrincipal actor, String action, String objectType, String objectId, String result,
                       Map<String, Object> detail) {
        record(actor, action, objectType, objectId, result, detail, null, null, TraceContext.current());
    }

    public PageData search(AuditQuery query, int page, int pageSize) {
        long total = repository.count(query);
        List<AuditEntry> items = repository.search(query, pageSize, (long) (page - 1) * pageSize);
        return new PageData(items.stream().map(this::toResponse).toList(), total, page, pageSize);
    }

    public String exportCsv(AuditQuery query) {
        verifyIntegrity();
        List<AuditEntry> entries = repository.search(query, 100000, 0);
        StringBuilder csv = new StringBuilder("\uFEFFid,tenantId,actorId,actorName,action,objectType,objectId,result,ip,traceId,createdAt,detail\n");
        for (AuditEntry e : entries) {
            String[] fields = {e.id(), e.tenantId(), e.actorId(), e.actorName(), e.action(), e.objectType(), e.objectId(),
                    e.result(), e.ip(), e.traceId(), e.createdAt().toString(), e.detailJson()};
            for (String field : fields) {
                csv.append(escapeCsv(field)).append(',');
            }
            csv.setLength(csv.length() - 1);
            csv.append("\r\n");
        }
        return csv.toString();
    }

    public void verifyIntegrity() {
        String previous = GENESIS;
        for (AuditEntry e : repository.allForIntegrity()) {
            if (!previous.equals(e.previousHash())) {
                throw new IllegalStateException("Audit chain is broken");
            }
            try {
                String expected = hash(canonical(e.id(), e.tenantId(), e.actorId(), e.actorName(), e.action(),
                        e.objectType(), e.objectId(), e.result(), e.ip(), e.userAgent(), e.traceId(), e.detailJson(),
                        e.previousHash(), e.env(), e.createdAt()));
                if (!java.security.MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8),
                        e.entryHash().getBytes(StandardCharsets.UTF_8))) {
                    throw new IllegalStateException("Audit entry authentication failed");
                }
            } catch (GeneralSecurityException ex) {
                throw new IllegalStateException("Audit verification failed", ex);
            }
            previous = e.entryHash();
        }
    }

    Map<String, Object> toResponse(AuditEntry e) {
        try {
            Object details = e.detailJson() == null ? Map.of() : objectMapper.readValue(e.detailJson(), Map.class);
            return e.toResponse(details);
        } catch (java.io.IOException ex) {
            throw new IllegalStateException("Invalid audit detail json", ex);
        }
    }

    private String canonical(String id, String tenantId, String actorId, String actorName, String action,
                             String objectType, String objectId, String result, String ip, String userAgent,
                             String traceId, String detail, String previous, String environment, OffsetDateTime createdAt) {
        return String.join("\n", nullToEmpty(id), nullToEmpty(tenantId), nullToEmpty(actorId), nullToEmpty(actorName),
                nullToEmpty(action), nullToEmpty(objectType), nullToEmpty(objectId), nullToEmpty(result), nullToEmpty(ip),
                nullToEmpty(userAgent), nullToEmpty(traceId), nullToEmpty(detail), nullToEmpty(previous),
                nullToEmpty(environment), createdAt.toString());
    }

    private String hash(String value) throws GeneralSecurityException {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(keyring.auditSecret(), "HmacSHA256"));
        return java.util.HexFormat.of().formatHex(mac.doFinal(value.getBytes(StandardCharsets.UTF_8)));
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> sanitize(Map<String, Object> input) {
        Map<String, Object> sorted = new TreeMap<>();
        if (input == null) {
            return sorted;
        }
        input.forEach((key, value) -> {
            if (key.toLowerCase(java.util.Locale.ROOT).matches(".*(password|secret|token|authorization|content|key).*")) {
                sorted.put(key, "[REDACTED]");
            } else if (value instanceof Map<?, ?> nested) {
                Map<String, Object> child = new LinkedHashMap<>();
                nested.forEach((k, v) -> child.put(String.valueOf(k), v));
                sorted.put(key, sanitize(child));
            } else {
                sorted.put(key, value);
            }
        });
        return sorted;
    }

    private static String escapeCsv(String value) {
        if (value == null) return "";
        String safe = value;
        if (!safe.isEmpty() && "=+-@".indexOf(safe.charAt(0)) >= 0) {
            safe = "'" + safe;
        }
        return '"' + safe.replace("\"", "\"\"") + '"';
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static String abbreviate(String value, int max) {
        if (value == null || value.length() <= max) return value;
        return value.substring(0, max);
    }

    public record PageData(List<Map<String, Object>> items, long total, int page, int pageSize) {
    }
}
