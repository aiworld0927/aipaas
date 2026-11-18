package com.aipaas.platform.knowledgebase;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Repository
public class KnowledgeBaseRepository {

    private static final String SELECT = """
            select k.id, k.tenant_id, k.name, k.description, k.domain, k.standard_system, k.config_json,
                   k.status, k.index_status, k.created_by, k.created_at, k.updated_at,
                   (select count(*) from documents d where d.tenant_id=k.tenant_id and d.kb_id=k.id
                      and d.deleted_at is null) as document_count,
                   (select coalesce(sum(d.chunk_count),0) from documents d where d.tenant_id=k.tenant_id
                      and d.kb_id=k.id and d.deleted_at is null) as chunk_count,
                   (select count(*) from documents d where d.tenant_id=k.tenant_id and d.kb_id=k.id
                      and d.deleted_at is null and d.status in ('QUEUED','PARSING','PROCESSING')) as active_docs,
                   (select count(*) from documents d where d.tenant_id=k.tenant_id and d.kb_id=k.id
                      and d.deleted_at is null and d.index_status in ('PENDING','BUILDING')) as pending_index,
                   (select count(*) from documents d where d.tenant_id=k.tenant_id and d.kb_id=k.id
                      and d.deleted_at is null and d.index_status='FAILED') as failed_index
            from knowledge_bases k
            """;

    private static final RowMapper<Row> MAPPER = (rs, i) -> new Row(
            rs.getString("id"), rs.getString("tenant_id"), rs.getString("name"), rs.getString("description"),
            rs.getString("domain"), rs.getString("standard_system"), rs.getString("config_json"),
            rs.getString("status"), rs.getString("index_status"), rs.getString("created_by"),
            rs.getObject("created_at", OffsetDateTime.class), rs.getObject("updated_at", OffsetDateTime.class),
            rs.getLong("document_count"), rs.getLong("chunk_count"), rs.getLong("active_docs"),
            rs.getLong("pending_index"), rs.getLong("failed_index"));

    private final JdbcTemplate jdbc;

    public KnowledgeBaseRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public List<Row> page(String tenantId, String keyword, String domain, int pageSize, long offset) {
        StringBuilder sql = new StringBuilder(SELECT)
                .append(" where k.tenant_id=? and k.deleted_at is null and k.status<>'DELETED'");
        List<Object> args = new ArrayList<>();
        args.add(tenantId);
        if (keyword != null && !keyword.isBlank()) {
            sql.append(" and (lower(k.name) like ? or lower(coalesce(k.description,'')) like ?)");
            String pattern = "%" + keyword.toLowerCase() + "%";
            args.add(pattern);
            args.add(pattern);
        }
        if (domain != null && !domain.isBlank()) {
            sql.append(" and k.domain=?");
            args.add(domain);
        }
        sql.append(" order by k.created_at desc limit ? offset ?");
        args.add(pageSize);
        args.add(offset);
        return jdbc.query(sql.toString(), MAPPER, args.toArray());
    }

    public long count(String tenantId, String keyword, String domain) {
        StringBuilder sql = new StringBuilder("select count(*) from knowledge_bases k "
                + "where k.tenant_id=? and k.deleted_at is null and k.status<>'DELETED'");
        List<Object> args = new ArrayList<>();
        args.add(tenantId);
        if (keyword != null && !keyword.isBlank()) {
            sql.append(" and (lower(k.name) like ? or lower(coalesce(k.description,'')) like ?)");
            String pattern = "%" + keyword.toLowerCase() + "%";
            args.add(pattern);
            args.add(pattern);
        }
        if (domain != null && !domain.isBlank()) {
            sql.append(" and k.domain=?");
            args.add(domain);
        }
        Long value = jdbc.queryForObject(sql.toString(), Long.class, args.toArray());
        return value == null ? 0 : value;
    }

    public Optional<Row> find(String tenantId, String kbId) {
        List<Row> rows = jdbc.query(SELECT + " where k.tenant_id=? and k.id=? and k.deleted_at is null",
                MAPPER, tenantId, kbId);
        return rows.stream().findFirst();
    }

    public boolean nameExists(String tenantId, String name) {
        Integer count = jdbc.queryForObject("select count(*) from knowledge_bases where tenant_id=? and name=? "
                + "and deleted_at is null and status<>'DELETED'", Integer.class, tenantId, name);
        return count != null && count > 0;
    }

    public void insert(String id, String tenantId, String name, String description, String domain,
                       String standardSystem, String configJson, String createdBy, OffsetDateTime now) {
        jdbc.update("insert into knowledge_bases(id,tenant_id,name,description,domain,standard_system,config_json,"
                        + "status,index_status,created_by,created_at,updated_at) values(?,?,?,?,?,?,?,'ACTIVE','EMPTY',?,?,?)",
                id, tenantId, name, description, domain, standardSystem, configJson, createdBy, now, now);
    }

    public void update(String tenantId, String kbId, String name, String description, String domain,
                       String standardSystem, String configJson, OffsetDateTime now) {
        jdbc.update("update knowledge_bases set name=?,description=?,domain=?,standard_system=?,config_json=?,"
                        + "updated_at=? where tenant_id=? and id=? and deleted_at is null",
                name, description, domain, standardSystem, configJson, now, tenantId, kbId);
    }

    public void markDeleted(String tenantId, String kbId, OffsetDateTime now) {
        jdbc.update("update knowledge_bases set status='DELETED',deleted_at=?,updated_at=? "
                + "where tenant_id=? and id=? and deleted_at is null", now, now, tenantId, kbId);
    }

    public record Row(String id, String tenantId, String name, String description, String domain,
                      String standardSystem, String configJson, String status, String indexStatus,
                      String createdBy, OffsetDateTime createdAt, OffsetDateTime updatedAt, long documentCount,
                      long chunkCount, long activeDocs, long pendingIndex, long failedIndex) {
    }
}
