package com.aipaas.platform.index;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/** #49 索引构建持久化与知识库状态同步。 */
@Repository
public class VectorIndexRepository {

    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    private final RowMapper<IndexBuildRow> mapper = this::map;

    public VectorIndexRepository(JdbcTemplate jdbc, ObjectMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    public void insert(IndexBuildRow row) {
        jdbc.update("""
                insert into vector_index_builds(id, tenant_id, kb_id, idempotency_key, status, progress,
                    index_type, metric, params_json, index_slot, resolved_slot, alias, model, version, env,
                    collection, previous_collection, version_num, dimension, total_chunks, indexed_chunks,
                    document_ids_json, attempt, created_at, updated_at)
                values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, row.id(), row.tenantId(), row.kbId(), row.idempotencyKey(), row.status(), row.progress(),
                row.spec().indexType(), row.spec().metric(), write(row.spec().params()), row.spec().indexSlot(),
                row.resolvedSlot(), row.spec().alias(), row.spec().model(), row.spec().version(), row.spec().env(),
                row.collection(), row.previousCollection(), row.versionNum(), row.dimension(), row.totalChunks(),
                row.indexedChunks(), row.documentIdsJson(), row.attempt(), row.createdAt(), row.updatedAt());
    }

    public Optional<IndexBuildRow> find(String tenantId, String id) {
        List<IndexBuildRow> rows = jdbc.query(
                "select * from vector_index_builds where tenant_id=? and id=?", mapper, tenantId, id);
        return rows.stream().findFirst();
    }

    public Optional<IndexBuildRow> findByIdempotencyKey(String tenantId, String key) {
        if (key == null || key.isBlank()) {
            return Optional.empty();
        }
        List<IndexBuildRow> rows = jdbc.query(
                "select * from vector_index_builds where tenant_id=? and idempotency_key=?",
                mapper, tenantId, key);
        return rows.stream().findFirst();
    }

    public void markRunning(String id, int progress, OffsetDateTime now) {
        jdbc.update("update vector_index_builds set status='RUNNING', progress=?, started_at=coalesce(started_at,?),"
                + " updated_at=? where id=?", progress, now, now, id);
    }

    public void markSucceeded(String id, int indexedChunks, long durationMs, String resourceJson,
                              OffsetDateTime now) {
        jdbc.update("update vector_index_builds set status='SUCCEEDED', progress=100, indexed_chunks=?,"
                        + " duration_ms=?, resource_json=?, error_message=null, finished_at=?, updated_at=? where id=?",
                indexedChunks, durationMs, resourceJson, now, now, id);
    }

    public void markFailed(String id, long durationMs, String resourceJson, String error, OffsetDateTime now) {
        String message = error == null ? null : error.substring(0, Math.min(error.length(), 512));
        jdbc.update("update vector_index_builds set status='FAILED', duration_ms=?, resource_json=?,"
                        + " error_message=?, finished_at=?, updated_at=? where id=?",
                durationMs, resourceJson, message, now, now, id);
    }

    public void requeue(String id, OffsetDateTime now) {
        jdbc.update("update vector_index_builds set status='QUEUED', progress=0, attempt=attempt+1,"
                        + " error_message=null, started_at=null, finished_at=null, duration_ms=null,"
                        + " resource_json=null, updated_at=? where id=?",
                now, id);
    }

    public void markRolledBack(String id, OffsetDateTime now) {
        jdbc.update("update vector_index_builds set rolled_back=true, updated_at=? where id=?", now, id);
    }

    public int nextVersion(String tenantId, String alias) {
        Integer max = jdbc.queryForObject(
                "select coalesce(max(version_num),0) from vector_index_builds where tenant_id=? and alias=?",
                Integer.class, tenantId, alias);
        return (max == null ? 0 : max) + 1;
    }

    public Optional<IndexBuildRow> latestSucceededForAlias(String tenantId, String alias) {
        List<IndexBuildRow> rows = jdbc.query(
                "select * from vector_index_builds where tenant_id=? and alias=? and status='SUCCEEDED'"
                        + " and rolled_back=false order by version_num desc limit 1",
                mapper, tenantId, alias);
        return rows.stream().findFirst();
    }

    public Optional<IndexBuildRow> latestSucceeded(String tenantId, String kbId) {
        List<IndexBuildRow> rows = jdbc.query(
                "select * from vector_index_builds where tenant_id=? and kb_id=? and status='SUCCEEDED'"
                        + " and rolled_back=false order by created_at desc limit 1",
                mapper, tenantId, kbId);
        return rows.stream().findFirst();
    }

    public void updateKnowledgeBaseStatus(String tenantId, String kbId, String status, OffsetDateTime now) {
        jdbc.update("update knowledge_bases set index_status=?, updated_at=? where tenant_id=? and id=?",
                status, now, tenantId, kbId);
    }

    public boolean knowledgeBaseExists(String tenantId, String kbId) {
        Integer count = jdbc.queryForObject(
                "select count(*) from knowledge_bases where tenant_id=? and id=? and deleted_at is null",
                Integer.class, tenantId, kbId);
        return count != null && count > 0;
    }

    public String knowledgeBaseIndexStatus(String tenantId, String kbId) {
        List<String> values = jdbc.queryForList(
                "select index_status from knowledge_bases where tenant_id=? and id=? and deleted_at is null",
                String.class, tenantId, kbId);
        return values.isEmpty() ? "EMPTY" : values.get(0);
    }

    public int documentCount(String tenantId, String kbId) {
        Integer count = jdbc.queryForObject(
                "select count(*) from documents where tenant_id=? and kb_id=? and deleted_at is null",
                Integer.class, tenantId, kbId);
        return count == null ? 0 : count;
    }

    /** 待索引 chunk 数：可按 documentIds 限定（P0 支持部分文档重建）。 */
    public int chunkTotal(String tenantId, String kbId, List<String> documentIds) {
        StringBuilder sql = new StringBuilder(
                "select coalesce(sum(chunk_count),0) from documents where tenant_id=? and kb_id=? and deleted_at is null");
        List<Object> args = new ArrayList<>(List.of(tenantId, kbId));
        if (documentIds != null && !documentIds.isEmpty()) {
            sql.append(" and id in (").append(String.join(",", java.util.Collections.nCopies(documentIds.size(), "?")))
                    .append(")");
            args.addAll(documentIds);
        }
        Integer total = jdbc.queryForObject(sql.toString(), Integer.class, args.toArray());
        return total == null ? 0 : total;
    }

    public long indexedChunkTotal(String tenantId, String kbId) {
        Long total = jdbc.queryForObject(
                "select coalesce(sum(chunk_count),0) from documents where tenant_id=? and kb_id=? and deleted_at is null",
                Long.class, tenantId, kbId);
        return total == null ? 0 : total;
    }

    private IndexBuildRow map(ResultSet rs, int rowNum) throws SQLException {
        Map<String, Object> params = read(rs.getString("params_json"), new TypeReference<>() {
        });
        IndexSpec spec = IndexSpec.of(rs.getString("kb_id"), rs.getString("index_type"), rs.getString("metric"),
                params, rs.getString("index_slot"), rs.getString("alias"), rs.getString("model"),
                rs.getString("version"), rs.getString("env"));
        return new IndexBuildRow(
                rs.getString("id"), rs.getString("tenant_id"), rs.getString("kb_id"),
                rs.getString("idempotency_key"), rs.getString("status"), rs.getInt("progress"), spec,
                rs.getString("resolved_slot"), rs.getString("collection"), rs.getString("previous_collection"),
                rs.getInt("version_num"), rs.getInt("dimension"), rs.getInt("total_chunks"),
                rs.getInt("indexed_chunks"), (Long) rs.getObject("duration_ms", Long.class),
                rs.getString("resource_json"), rs.getString("document_ids_json"), rs.getInt("attempt"),
                rs.getBoolean("rolled_back"), rs.getString("error_message"),
                rs.getObject("started_at", OffsetDateTime.class),
                rs.getObject("finished_at", OffsetDateTime.class),
                rs.getObject("created_at", OffsetDateTime.class),
                rs.getObject("updated_at", OffsetDateTime.class));
    }

    private String write(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception ex) {
            throw new IllegalStateException("序列化失败", ex);
        }
    }

    private <T> T read(String value, TypeReference<T> type) {
        try {
            return value == null || value.isBlank() ? json.readValue("{}", type) : json.readValue(value, type);
        } catch (Exception ex) {
            return json.convertValue(Map.of(), type);
        }
    }

    static OffsetDateTime now() {
        return OffsetDateTime.now(ZoneOffset.UTC);
    }
}
