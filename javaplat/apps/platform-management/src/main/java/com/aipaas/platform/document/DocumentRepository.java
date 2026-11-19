package com.aipaas.platform.document;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public class DocumentRepository {

    private static final RowMapper<DocumentRow> DOCUMENT_MAPPER = (rs, i) -> new DocumentRow(
            rs.getString("id"), rs.getString("tenant_id"), rs.getString("kb_id"), rs.getString("filename_ciphertext"),
            rs.getString("standard_no_ciphertext"), rs.getString("object_key"), rs.getString("format"),
            rs.getLong("size_bytes"), rs.getString("status"), rs.getString("file_hash"),
            rs.getString("index_status"), rs.getInt("chunk_count"), rs.getString("error_code"),
            rs.getString("error_message"), rs.getString("uploader_id"),
            rs.getObject("created_at", OffsetDateTime.class), rs.getObject("updated_at", OffsetDateTime.class));

    private static final RowMapper<UploadSessionRow> SESSION_MAPPER = (rs, i) -> new UploadSessionRow(
            rs.getString("id"), rs.getString("tenant_id"), rs.getString("kb_id"),
            rs.getString("file_name_ciphertext"), rs.getString("file_hash"), rs.getLong("size_bytes"),
            rs.getLong("part_size"), rs.getInt("total_parts"), rs.getString("standard_no_ciphertext"),
            rs.getBoolean("auto_parse"), rs.getString("status"), rs.getString("object_key"),
            rs.getString("created_by"), rs.getObject("created_at", OffsetDateTime.class),
            rs.getObject("updated_at", OffsetDateTime.class));

    private final JdbcTemplate jdbc;

    public DocumentRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public boolean knowledgeBaseExists(String tenantId, String kbId) {
        Integer count = jdbc.queryForObject("select count(*) from knowledge_bases where tenant_id=? and id=? "
                + "and status='ACTIVE' and deleted_at is null", Integer.class, tenantId, kbId);
        return count != null && count > 0;
    }

    public void insertDocument(String id, String tenantId, String kbId, String filenameCiphertext,
                               String standardNoCiphertext, String objectKey, String format, long sizeBytes,
                               String fileHash, String uploaderId, OffsetDateTime now) {
        jdbc.update("insert into documents(id,tenant_id,kb_id,filename_ciphertext,standard_no_ciphertext,object_key,format,"
                        + "size_bytes,status,file_hash,index_status,chunk_count,uploader_id,created_at,updated_at) "
                        + "values(?,?,?,?,?,?,?,?,?,?,'PENDING',0,?,?,?)",
                id, tenantId, kbId, filenameCiphertext, standardNoCiphertext, objectKey, format, sizeBytes, "QUEUED",
                fileHash, uploaderId, now, now);
    }

    public void insertTask(String id, String tenantId, String documentId, String kbId, String stage, OffsetDateTime now) {
        jdbc.update("insert into ingest_tasks(id,tenant_id,document_id,kb_id,status,stage,progress,retry_count,max_retries,"
                        + "queued_at,created_at,updated_at) values(?,?,?,?,'QUEUED',?,0,0,2,?,?,?)",
                id, tenantId, documentId, kbId, stage, now, now, now);
    }

    public Optional<DocumentRow> findActive(String tenantId, String documentId) {
        List<DocumentRow> rows = jdbc.query("select * from documents where tenant_id=? and id=? and deleted_at is null",
                DOCUMENT_MAPPER, tenantId, documentId);
        return rows.stream().findFirst();
    }

    public Optional<DocumentRow> findActiveByHash(String tenantId, String kbId, String fileHash) {
        List<DocumentRow> rows = jdbc.query("select * from documents where tenant_id=? and kb_id=? and file_hash=? "
                + "and deleted_at is null order by created_at desc limit 1", DOCUMENT_MAPPER, tenantId, kbId, fileHash);
        return rows.stream().findFirst();
    }

    public List<DocumentRow> listActive(String tenantId, String kbId, String status) {
        StringBuilder sql = new StringBuilder("select * from documents where tenant_id=? and deleted_at is null");
        java.util.List<Object> args = new java.util.ArrayList<>();
        args.add(tenantId);
        if (kbId != null && !kbId.isBlank()) {
            sql.append(" and kb_id=?");
            args.add(kbId);
        }
        if (status != null && !status.isBlank()) {
            sql.append(" and status=?");
            args.add(status);
        }
        return jdbc.query(sql + " order by created_at desc", DOCUMENT_MAPPER, args.toArray());
    }

    public List<DocumentRow> activeDocuments(String tenantId, String kbId) {
        return jdbc.query("select * from documents where tenant_id=? and kb_id=? and deleted_at is null",
                DOCUMENT_MAPPER, tenantId, kbId);
    }

    public void softDelete(String tenantId, String documentId, OffsetDateTime now) {
        jdbc.update("update documents set deleted_at=?,index_status='DELETED',updated_at=? "
                + "where tenant_id=? and id=? and deleted_at is null", now, now, tenantId, documentId);
        jdbc.update("update ingest_tasks set status='CANCELLED',updated_at=? where tenant_id=? and document_id=? "
                + "and status in ('QUEUED','RUNNING')", now, tenantId, documentId);
    }

    public void softDeleteByKb(String tenantId, String kbId, OffsetDateTime now) {
        jdbc.update("update documents set deleted_at=?,index_status='DELETED',updated_at=? "
                + "where tenant_id=? and kb_id=? and deleted_at is null", now, now, tenantId, kbId);
        jdbc.update("update ingest_tasks set status='CANCELLED',updated_at=? where tenant_id=? and kb_id=? "
                + "and status in ('QUEUED','RUNNING')", now, tenantId, kbId);
    }

    public void markIndexStatus(String tenantId, String documentId, String status, OffsetDateTime now) {
        jdbc.update("update documents set index_status=?,updated_at=? where tenant_id=? and id=?",
                status, now, tenantId, documentId);
    }

    public Optional<TaskRow> latestTask(String tenantId, String documentId) {
        List<TaskRow> rows = jdbc.query("select id,status,stage,progress from ingest_tasks where tenant_id=? "
                        + "and document_id=? order by created_at desc limit 1",
                (rs, i) -> new TaskRow(rs.getString("id"), rs.getString("status"), rs.getString("stage"),
                        rs.getInt("progress")), tenantId, documentId);
        return rows.stream().findFirst();
    }

    /* ------------------------------ 分片上传 ------------------------------ */

    public void insertSession(String id, String tenantId, String kbId, String fileNameCiphertext, String fileHash,
                              long sizeBytes, long partSize, int totalParts, String standardNoCiphertext,
                              boolean autoParse, String objectKey, String createdBy, OffsetDateTime now) {
        jdbc.update("insert into document_upload_sessions(id,tenant_id,kb_id,file_name_ciphertext,file_hash,size_bytes,"
                        + "part_size,total_parts,standard_no_ciphertext,auto_parse,status,object_key,created_by,"
                        + "created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?,'UPLOADING',?,?,?,?)",
                id, tenantId, kbId, fileNameCiphertext, fileHash, sizeBytes, partSize, totalParts,
                standardNoCiphertext, autoParse, objectKey, createdBy, now, now);
    }

    public Optional<UploadSessionRow> findSession(String tenantId, String kbId, String uploadId) {
        List<UploadSessionRow> rows = jdbc.query("select * from document_upload_sessions where tenant_id=? and kb_id=? "
                + "and id=?", SESSION_MAPPER, tenantId, kbId, uploadId);
        return rows.stream().findFirst();
    }

    public Optional<UploadSessionRow> findOpenSessionByHash(String tenantId, String kbId, String fileHash) {
        List<UploadSessionRow> rows = jdbc.query("select * from document_upload_sessions where tenant_id=? and kb_id=? "
                        + "and file_hash=? and status='UPLOADING' order by created_at desc limit 1",
                SESSION_MAPPER, tenantId, kbId, fileHash);
        return rows.stream().findFirst();
    }

    public List<PartRow> parts(String uploadId) {
        return jdbc.query("select part_no,offset_bytes,size_bytes,object_key,part_hash from document_upload_parts "
                        + "where upload_id=? order by part_no",
                (rs, i) -> new PartRow(rs.getInt("part_no"), rs.getLong("offset_bytes"), rs.getLong("size_bytes"),
                        rs.getString("object_key"), rs.getString("part_hash")), uploadId);
    }

    public void savePart(String uploadId, int partNo, long offset, long sizeBytes, String objectKey, String partHash,
                         OffsetDateTime now) {
        jdbc.update("delete from document_upload_parts where upload_id=? and part_no=?", uploadId, partNo);
        jdbc.update("insert into document_upload_parts(upload_id,part_no,offset_bytes,size_bytes,object_key,part_hash,"
                        + "created_at) values(?,?,?,?,?,?,?)",
                uploadId, partNo, offset, sizeBytes, objectKey, partHash, now);
        jdbc.update("update document_upload_sessions set updated_at=? where id=?", now, uploadId);
    }

    public void completeSession(String uploadId, OffsetDateTime now) {
        jdbc.update("update document_upload_sessions set status='COMPLETED',updated_at=? where id=?", now, uploadId);
    }

    public void touchSession(String uploadId, OffsetDateTime now) {
        jdbc.update("update document_upload_sessions set updated_at=? where id=?", now, uploadId);
    }

    public record DocumentRow(String id, String tenantId, String kbId, String filenameCiphertext,
                              String standardNoCiphertext, String objectKey, String format, long sizeBytes,
                              String status, String fileHash, String indexStatus, int chunkCount, String errorCode,
                              String errorMessage, String uploaderId, OffsetDateTime createdAt,
                              OffsetDateTime updatedAt) {
    }

    public record UploadSessionRow(String id, String tenantId, String kbId, String fileNameCiphertext,
                                   String fileHash, long sizeBytes, long partSize, int totalParts,
                                   String standardNoCiphertext, boolean autoParse, String status, String objectKey,
                                   String createdBy, OffsetDateTime createdAt, OffsetDateTime updatedAt) {
    }

    public record PartRow(int partNo, long offset, long sizeBytes, String objectKey, String partHash) {
    }

    public record TaskRow(String id, String status, String stage, int progress) {
    }
}
