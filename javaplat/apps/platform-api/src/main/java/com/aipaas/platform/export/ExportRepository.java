package com.aipaas.platform.export;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

/** export_jobs 持久化：状态机 QUEUED → RUNNING → SUCCEEDED/FAILED，失败可重试。 */
@Repository
public class ExportRepository {

    private static final RowMapper<ExportJob> MAPPER = (rs, i) -> new ExportJob(
            rs.getString("id"), rs.getString("tenant_id"), rs.getString("kb_id"), rs.getString("document_id"),
            rs.getString("export_type"), rs.getString("format"), rs.getString("status"), rs.getInt("progress"),
            rs.getString("scope"), rs.getInt("clause_count"), rs.getInt("retry_count"), rs.getInt("max_retries"),
            rs.getString("actor_id"), rs.getString("idempotency_key"), rs.getString("object_key"),
            rs.getString("file_name"), rs.getLong("size_bytes"), rs.getString("file_hash"),
            rs.getString("error_code"), rs.getString("error_message"),
            rs.getObject("expires_at", OffsetDateTime.class), rs.getObject("started_at", OffsetDateTime.class),
            rs.getObject("finished_at", OffsetDateTime.class), rs.getObject("created_at", OffsetDateTime.class),
            rs.getObject("updated_at", OffsetDateTime.class));

    private final JdbcTemplate jdbc;

    public ExportRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public void insert(ExportJob job) {
        jdbc.update("insert into export_jobs(id,tenant_id,kb_id,document_id,export_type,format,status,progress,scope,"
                        + "clause_count,retry_count,max_retries,actor_id,idempotency_key,created_at,updated_at) "
                        + "values(?,?,?,?,?,?,'QUEUED',0,?,0,0,?,?,?,?,?)",
                job.id(), job.tenantId(), job.kbId(), job.documentId(), job.exportType(), job.format(), job.scope(),
                job.maxRetries(), job.actorId(), job.idempotencyKey(), job.createdAt(), job.updatedAt());
    }

    public Optional<ExportJob> find(String tenantId, String id) {
        return jdbc.query("select * from export_jobs where tenant_id=? and id=?", MAPPER, tenantId, id)
                .stream().findFirst();
    }

    public ExportJob findById(String id) {
        return jdbc.query("select * from export_jobs where id=?", MAPPER, id).stream().findFirst().orElse(null);
    }

    public Optional<ExportJob> findByIdempotency(String tenantId, String actorId, String key) {
        if (key == null || key.isBlank()) {
            return Optional.empty();
        }
        return jdbc.query("select * from export_jobs where tenant_id=? and actor_id=? and idempotency_key=?",
                MAPPER, tenantId, actorId, key).stream().findFirst();
    }

    /** 认领任务；并发下只有一个执行者能把 QUEUED 改成 RUNNING。 */
    public boolean claim(String id, OffsetDateTime now) {
        return jdbc.update("update export_jobs set status='RUNNING',progress=10,started_at=?,updated_at=? "
                + "where id=? and status='QUEUED'", now, now, id) == 1;
    }

    public void progress(String id, int progress, OffsetDateTime now) {
        jdbc.update("update export_jobs set progress=?,updated_at=? where id=?", progress, now, id);
    }

    public void succeed(String id, int clauseCount, String objectKey, String fileName, long sizeBytes,
                        String fileHash, OffsetDateTime expiresAt, OffsetDateTime now) {
        jdbc.update("update export_jobs set status='SUCCEEDED',progress=100,clause_count=?,object_key=?,file_name=?,"
                        + "size_bytes=?,file_hash=?,expires_at=?,error_code=null,error_message=null,finished_at=?,"
                        + "updated_at=? where id=?",
                clauseCount, objectKey, fileName, sizeBytes, fileHash, expiresAt, now, now, id);
    }

    public void retryLater(String id, String errorCode, String errorMessage, OffsetDateTime now) {
        jdbc.update("update export_jobs set status='QUEUED',progress=0,retry_count=retry_count+1,error_code=?,"
                + "error_message=?,updated_at=? where id=?", errorCode, errorMessage, now, id);
    }

    public void fail(String id, String errorCode, String errorMessage, OffsetDateTime now) {
        jdbc.update("update export_jobs set status='FAILED',error_code=?,error_message=?,finished_at=?,updated_at=? "
                + "where id=?", errorCode, errorMessage, now, now, id);
    }

    public void markExpired(String id, OffsetDateTime now) {
        jdbc.update("update export_jobs set status='EXPIRED',updated_at=? where id=?", now, id);
    }

    /** 人工重试：清空上次产物与错误，重新排队。 */
    public int requeue(String id, String actorId, OffsetDateTime now) {
        return jdbc.update("update export_jobs set status='QUEUED',progress=0,retry_count=0,actor_id=?,"
                + "object_key=null,file_name=null,size_bytes=0,file_hash=null,error_code=null,error_message=null,"
                + "expires_at=null,started_at=null,finished_at=null,updated_at=? where id=?", actorId, now, id);
    }

    public List<ExportJob> expiredBefore(OffsetDateTime now, int limit) {
        return jdbc.query("select * from export_jobs where status='SUCCEEDED' and expires_at is not null "
                + "and expires_at<=? order by expires_at limit ?", MAPPER, now, limit);
    }
}
