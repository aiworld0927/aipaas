package com.aipaas.platform.document;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.io.InputStream;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

/**
 * 文档软删除后的索引与对象清理。接口在 P0 保持稳定，P1 接入真实向量库时只替换 VectorIndexGateway。
 */
@Service
public class IndexCleanupService {

    private static final Logger log = LoggerFactory.getLogger(IndexCleanupService.class);

    private final JdbcTemplate jdbc;
    private final EncryptedObjectStore objectStore;
    private final VectorIndexGateway vectorIndex;

    public IndexCleanupService(JdbcTemplate jdbc, EncryptedObjectStore objectStore, VectorIndexGateway vectorIndex) {
        this.jdbc = jdbc;
        this.objectStore = objectStore;
        this.vectorIndex = vectorIndex;
    }

    public void enqueue(String tenantId, String documentId, String kbId, String objectKey) {
        OffsetDateTime now = OffsetDateTime.now();
        jdbc.update("insert into index_cleanup_tasks(id,tenant_id,document_id,kb_id,object_key,status,attempts,"
                        + "max_attempts,next_attempt_at,created_at,updated_at) values(?,?,?,?,?,'PENDING',0,2,?,?,?)",
                "cln_" + UUID.randomUUID().toString().replace("-", ""), tenantId, documentId, kbId, objectKey,
                now, now, now);
    }

    @Scheduled(fixedDelayString = "${aipaas.index.cleanup-interval-ms:5000}")
    public void scheduled() {
        processOnce(20);
    }

    public int processOnce(int limit) {
        List<Task> tasks = jdbc.query("select id,tenant_id,document_id,kb_id,object_key,attempts,max_attempts "
                        + "from index_cleanup_tasks where status='PENDING' and (next_attempt_at is null or next_attempt_at<=?) "
                        + "order by created_at limit ?",
                (rs, i) -> new Task(rs.getString("id"), rs.getString("tenant_id"), rs.getString("document_id"),
                        rs.getString("kb_id"), rs.getString("object_key"), rs.getInt("attempts"),
                        rs.getInt("max_attempts")), OffsetDateTime.now(), limit);
        int done = 0;
        for (Task task : tasks) {
            try {
                vectorIndex.deleteDocument(task.tenantId(), task.kbId(), task.documentId());
                if (task.objectKey() != null && !task.objectKey().isBlank()) {
                    objectStore.delete(task.objectKey());
                }
                jdbc.update("update index_cleanup_tasks set status='DONE',updated_at=? where id=?",
                        OffsetDateTime.now(), task.id());
                done++;
            } catch (RuntimeException ex) {
                int attempts = task.attempts() + 1;
                boolean exhausted = attempts >= task.maxAttempts();
                jdbc.update("update index_cleanup_tasks set attempts=?,status=?,last_error=?,next_attempt_at=?,updated_at=? "
                                + "where id=?", attempts, exhausted ? "FAILED" : "PENDING",
                        abbreviate(ex.getClass().getSimpleName()), OffsetDateTime.now().plusSeconds(30L * attempts),
                        OffsetDateTime.now(), task.id());
                log.warn("index cleanup retry scheduled documentId={} attempts={}", task.documentId(), attempts);
            }
        }
        return done;
    }

    /** 用于验证对象密文可读且不落明文；生产路径不调用。 */
    boolean objectReadable(String objectKey) {
        try (InputStream ignored = objectStore.open(objectKey)) {
            return true;
        } catch (Exception ex) {
            return false;
        }
    }

    private static String abbreviate(String value) {
        return value != null && value.length() > 512 ? value.substring(0, 512) : value;
    }

    private record Task(String id, String tenantId, String documentId, String kbId, String objectKey, int attempts,
                        int maxAttempts) {
    }
}
