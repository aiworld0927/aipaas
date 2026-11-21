package com.aipaas.platform.tenant;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

/** 租户台账；新租户创建时复用默认租户的权限目录与内置角色。 */
@Repository
public class TenantRepository {

    private final JdbcTemplate jdbc;

    public TenantRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private final RowMapper<TenantRow> mapper = (rs, n) -> new TenantRow(
            rs.getString("id"), rs.getString("name"), rs.getString("status"), rs.getString("description"),
            rs.getObject("created_at", OffsetDateTime.class), rs.getObject("updated_at", OffsetDateTime.class));

    public List<TenantRow> list() {
        return jdbc.query("select * from tenants order by created_at", mapper);
    }

    public Optional<TenantRow> find(String id) {
        return jdbc.query("select * from tenants where id=?", mapper, id).stream().findFirst();
    }

    public long countUsers(String tenantId) {
        Long count = jdbc.queryForObject("select count(*) from sec_users where tenant_id=?", Long.class, tenantId);
        return count == null ? 0 : count;
    }

    public void create(String id, String name, String description, OffsetDateTime now) {
        jdbc.update("insert into tenants(id,name,status,description,created_at,updated_at) values(?,?, 'ACTIVE',?,?,?)",
                id, name, description, now, now);
    }

    public int update(String id, String name, String status, String description, OffsetDateTime now) {
        return jdbc.update("update tenants set name=?, status=?, description=?, updated_at=? where id=?",
                name, status, description, now, id);
    }

    /** 复制默认租户的权限点、内置角色、知识库与角色授权，保证新租户可直接注册使用。 */
    public void provision(String tenantId, OffsetDateTime now) {
        jdbc.update("insert into sec_permissions(tenant_id,code,name,domain,description) "
                + "select ?, code, name, domain, description from sec_permissions where tenant_id='default' "
                + "on conflict (tenant_id, code) do nothing", tenantId);
        String kbId = "kb_" + tenantId;
        jdbc.update("insert into knowledge_bases(id,tenant_id,name,status,created_at,updated_at) "
                + "values(?,?,?,?,?,?) on conflict (id) do nothing",
                kbId, tenantId, "默认金融标准库", "ACTIVE", now, now);
        provisionRole(tenantId, "PLATFORM_ADMIN", "平台管理员", "rol_platform_admin");
        provisionRole(tenantId, "KB_ADMIN", "知识库管理员", "rol_kb_admin");
        provisionRole(tenantId, "KB_UPLOADER", "知识库上传员", "rol_kb_uploader");
        provisionRole(tenantId, "AUDITOR", "安全审计员", "rol_auditor");
        String adminRoleId = roleId(tenantId, "PLATFORM_ADMIN");
        String kbAdminRoleId = roleId(tenantId, "KB_ADMIN");
        String uploaderRoleId = roleId(tenantId, "KB_UPLOADER");
        grantKb(tenantId, adminRoleId, kbId, "kb:manage");
        grantKb(tenantId, kbAdminRoleId, kbId, "kb:manage");
        grantKb(tenantId, kbAdminRoleId, kbId, "index:read");
        grantKb(tenantId, kbAdminRoleId, kbId, "index:build");
        grantKb(tenantId, kbAdminRoleId, kbId, "retrieval:query");
        grantKb(tenantId, uploaderRoleId, kbId, "doc:upload");
    }

    private void provisionRole(String tenantId, String code, String name, String templateRoleId) {
        String id = roleId(tenantId, code);
        Integer exists = jdbc.queryForObject("select count(*) from sec_roles where tenant_id=? and code=?",
                Integer.class, tenantId, code);
        if (exists != null && exists > 0) return;
        OffsetDateTime now = OffsetDateTime.now(java.time.ZoneOffset.UTC);
        jdbc.update("insert into sec_roles(id,tenant_id,code,name,built_in,created_at,updated_at) "
                + "values(?,?,?,?,true,?,?)", id, tenantId, code, name, now, now);
        jdbc.update("insert into sec_role_permissions(tenant_id,role_id,permission_code) "
                + "select ?, ?, permission_code from sec_role_permissions where tenant_id='default' and role_id=? "
                + "on conflict do nothing", tenantId, id, templateRoleId);
    }

    private void grantKb(String tenantId, String roleId, String kbId, String permission) {
        jdbc.update("insert into sec_role_kb_grants(tenant_id,role_id,kb_id,permission_code) values(?,?,?,?) "
                + "on conflict do nothing", tenantId, roleId, kbId, permission);
    }

    private static String roleId(String tenantId, String code) {
        return "rol_" + tenantId + "_" + code.toLowerCase();
    }

    public record TenantRow(String id, String name, String status, String description,
                            OffsetDateTime createdAt, OffsetDateTime updatedAt) {
    }
}
