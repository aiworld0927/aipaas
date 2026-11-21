package com.aipaas.platform.document;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

/** 文档级 ACL：按用户或角色授予 READ / WRITE。 */
@Repository
public class DocumentGrantRepository {

    private final JdbcTemplate jdbc;

    public DocumentGrantRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private final RowMapper<GrantRow> mapper = (rs, n) -> new GrantRow(
            rs.getString("subject_type"), rs.getString("subject_id"), rs.getString("permission"),
            rs.getString("granted_by"), rs.getObject("created_at", OffsetDateTime.class));

    public List<GrantRow> list(String tenantId, String documentId) {
        return jdbc.query("select * from document_grants where tenant_id=? and document_id=? "
                        + "order by subject_type, subject_id, permission",
                mapper, tenantId, documentId);
    }

    public void replace(String tenantId, String documentId, String grantedBy, List<GrantInput> grants) {
        jdbc.update("delete from document_grants where tenant_id=? and document_id=?", tenantId, documentId);
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        for (GrantInput grant : grants) {
            jdbc.update("insert into document_grants(id,tenant_id,document_id,subject_type,subject_id,permission,"
                            + "granted_by,created_at) values(?,?,?,?,?,?,?,?)",
                    "dgr_" + UUID.randomUUID().toString().replace("-", ""), tenantId, documentId,
                    grant.subjectType(), grant.subjectId(), grant.permission(), grantedBy, now);
        }
    }

    public boolean subjectExists(String tenantId, String subjectType, String subjectId) {
        String table = "USER".equals(subjectType) ? "sec_users" : "sec_roles";
        Integer count = jdbc.queryForObject("select count(*) from " + table + " where tenant_id=? and id=?",
                Integer.class, tenantId, subjectId);
        return count != null && count > 0;
    }

    public record GrantRow(String subjectType, String subjectId, String permission, String grantedBy,
                           OffsetDateTime createdAt) {
    }

    public record GrantInput(String subjectType, String subjectId, String permission) {
    }
}
