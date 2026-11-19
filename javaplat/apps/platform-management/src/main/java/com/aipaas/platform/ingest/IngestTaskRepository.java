package com.aipaas.platform.ingest;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/** 接入任务与文档解析状态的数据访问。 */
@Repository
public class IngestTaskRepository {

    private static final RowMapper<TaskRow> TASK_MAPPER = (rs, i) -> new TaskRow(
            rs.getString("id"), rs.getString("tenant_id"), rs.getString("document_id"), rs.getString("kb_id"),
            rs.getString("status"), rs.getString("stage"), rs.getInt("progress"), rs.getInt("retry_count"),
            rs.getInt("max_retries"), rs.getString("error_code"), rs.getString("error_message"),
            (Boolean) rs.getObject("retryable"), rs.getObject("queued_at", OffsetDateTime.class),
            rs.getObject("started_at", OffsetDateTime.class), rs.getObject("finished_at", OffsetDateTime.class),
            rs.getObject("created_at", OffsetDateTime.class), rs.getObject("updated_at", OffsetDateTime.class));

    private final JdbcTemplate jdbc;

    public IngestTaskRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<TaskRow> find(String tenantId, String taskId) {
        List<TaskRow> rows = jdbc.query("select * from ingest_tasks where tenant_id=? and id=?",
                TASK_MAPPER, tenantId, taskId);
        return rows.stream().findFirst();
    }

    public Optional<TaskRow> findById(String taskId) {
        List<TaskRow> rows = jdbc.query("select * from ingest_tasks where id=?", TASK_MAPPER, taskId);
        return rows.stream().findFirst();
    }

    public List<TaskRow> list(String tenantId, String kbId, String documentId, String status, int limit, int offset) {
        StringBuilder sql = new StringBuilder("select * from ingest_tasks where tenant_id=?");
        List<Object> args = new ArrayList<>();
        args.add(tenantId);
        if (kbId != null && !kbId.isBlank()) {
            sql.append(" and kb_id=?");
            args.add(kbId);
        }
        if (documentId != null && !documentId.isBlank()) {
            sql.append(" and document_id=?");
            args.add(documentId);
        }
        if (status != null && !status.isBlank()) {
            sql.append(" and status=?");
            args.add(status);
        }
        sql.append(" order by created_at desc limit ? offset ?");
        args.add(limit);
        args.add(offset);
        return jdbc.query(sql.toString(), TASK_MAPPER, args.toArray());
    }

    public int count(String tenantId, String kbId, String documentId, String status) {
        StringBuilder sql = new StringBuilder("select count(*) from ingest_tasks where tenant_id=?");
        List<Object> args = new ArrayList<>();
        args.add(tenantId);
        if (kbId != null && !kbId.isBlank()) {
            sql.append(" and kb_id=?");
            args.add(kbId);
        }
        if (documentId != null && !documentId.isBlank()) {
            sql.append(" and document_id=?");
            args.add(documentId);
        }
        if (status != null && !status.isBlank()) {
            sql.append(" and status=?");
            args.add(status);
        }
        Integer value = jdbc.queryForObject(sql.toString(), Integer.class, args.toArray());
        return value == null ? 0 : value;
    }

    /** 乐观认领：只有 QUEUED 任务能被推进到 RUNNING，重复消费直接返回 false。 */
    public boolean claim(String taskId, OffsetDateTime now) {
        return jdbc.update("update ingest_tasks set status='RUNNING',stage='PARSE',progress=10,started_at=?,"
                        + "error_code=null,error_message=null,retryable=null,updated_at=? "
                        + "where id=? and status='QUEUED' and stage='PARSE'", now, now, taskId) == 1;
    }

    /** 手动重试：复用同一任务行，仅 FAILED 可重试，避免产生重复任务。 */
    public boolean requeueForRetry(String taskId, OffsetDateTime now) {
        return jdbc.update("update ingest_tasks set status='QUEUED',stage='PARSE',progress=0,"
                        + "retry_count=retry_count+1,started_at=null,finished_at=null,error_code=null,"
                        + "error_message=null,retryable=null,queued_at=?,updated_at=? "
                        + "where id=? and status='FAILED'", now, now, taskId) == 1;
    }

    /** 传输层异常自动重试：仅当未超过 max_retries 时重新排队。 */
    public boolean requeueAuto(String taskId, OffsetDateTime now) {
        return jdbc.update("update ingest_tasks set status='QUEUED',stage='PARSE',progress=0,"
                        + "retry_count=retry_count+1,started_at=null,finished_at=null,error_code=null,"
                        + "error_message=null,retryable=null,queued_at=?,updated_at=? "
                        + "where id=? and status='RUNNING' and retry_count<max_retries", now, now, taskId) == 1;
    }

    public void markSucceeded(String taskId, OffsetDateTime now) {
        jdbc.update("update ingest_tasks set status='SUCCEEDED',stage='DONE',progress=100,finished_at=?,"
                        + "error_code=null,error_message=null,retryable=null,updated_at=? where id=?",
                now, now, taskId);
    }

    public void markFailed(String taskId, String code, String message, boolean retryable, OffsetDateTime now) {
        jdbc.update("update ingest_tasks set status='FAILED',progress=100,finished_at=?,error_code=?,"
                        + "error_message=?,retryable=?,updated_at=? where id=?",
                now, code, truncate(message), retryable, now, taskId);
    }

    public List<String> queuedTaskIds(int limit) {
        return jdbc.queryForList("select id from ingest_tasks where status='QUEUED' and stage='PARSE' "
                + "order by queued_at limit ?", String.class, limit);
    }

    public Optional<DocumentRow> findDocument(String tenantId, String documentId) {
        List<DocumentRow> rows = jdbc.query("select id,tenant_id,kb_id,filename_ciphertext,"
                        + "standard_no_ciphertext,object_key,format from documents "
                        + "where tenant_id=? and id=? and deleted_at is null",
                (rs, i) -> new DocumentRow(rs.getString("id"), rs.getString("tenant_id"), rs.getString("kb_id"),
                        rs.getString("filename_ciphertext"), rs.getString("standard_no_ciphertext"),
                        rs.getString("object_key"), rs.getString("format")),
                tenantId, documentId);
        return rows.stream().findFirst();
    }

    public void markDocument(String tenantId, String documentId, String status, String errorCode,
                             String errorMessage, OffsetDateTime now) {
        jdbc.update("update documents set status=?,error_code=?,error_message=?,updated_at=? "
                        + "where tenant_id=? and id=? and deleted_at is null",
                status, errorCode, truncate(errorMessage), now, tenantId, documentId);
    }

    public void markDocumentReady(String tenantId, String documentId, int chunkCount, OffsetDateTime now) {
        jdbc.update("update documents set status='READY',chunk_count=?,error_code=null,error_message=null,"
                        + "updated_at=? where tenant_id=? and id=? and deleted_at is null",
                chunkCount, now, tenantId, documentId);
    }

    private static String truncate(String value) {
        return value != null && value.length() > 512 ? value.substring(0, 512) : value;
    }

    public record TaskRow(String id, String tenantId, String documentId, String kbId, String status, String stage,
                          int progress, int retryCount, int maxRetries, String errorCode, String errorMessage,
                          Boolean retryable, OffsetDateTime queuedAt, OffsetDateTime startedAt,
                          OffsetDateTime finishedAt, OffsetDateTime createdAt, OffsetDateTime updatedAt) {
    }

    public record DocumentRow(String id, String tenantId, String kbId, String filenameCiphertext,
                              String standardNoCiphertext, String objectKey, String format) {
    }
}
