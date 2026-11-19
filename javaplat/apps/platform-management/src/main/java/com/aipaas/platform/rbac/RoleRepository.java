package com.aipaas.platform.rbac;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Repository
public class RoleRepository {

    private final JdbcTemplate jdbc;

    public RoleRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public List<Map<String, Object>> permissions(String tenantId) {
        return jdbc.query("select code,name,domain,description,tenant_id from sec_permissions where tenant_id=? order by code",
                (rs, n) -> Map.of("code", rs.getString("code"), "name", rs.getString("name"),
                        "domain", rs.getString("domain"), "description", rs.getString("description") == null ? "" : rs.getString("description"),
                        "tenantId", rs.getString("tenant_id")), tenantId);
    }

    public long countRoles(String tenantId) {
        Long count = jdbc.queryForObject("select count(*) from sec_roles where tenant_id=?", Long.class, tenantId);
        return count == null ? 0 : count;
    }

    public List<RoleView> pageRoles(String tenantId, int page, int pageSize) {
        return jdbc.query("select * from sec_roles where tenant_id=? order by code limit ? offset ?",
                (rs, n) -> mapRole(rs), tenantId, pageSize, (page - 1) * pageSize).stream().map(this::enrich).toList();
    }

    public Optional<RoleView> findById(String tenantId, String roleId) {
        return jdbc.query("select * from sec_roles where tenant_id=? and id=?", (rs, n) -> mapRole(rs), tenantId, roleId)
                .stream().findFirst().map(this::enrich);
    }

    public Optional<RoleView> findByCode(String tenantId, String code) {
        return jdbc.query("select * from sec_roles where tenant_id=? and code=?", (rs, n) -> mapRole(rs), tenantId, code)
                .stream().findFirst().map(this::enrich);
    }

    public void createRole(String id, String tenantId, String code, String name, boolean builtIn) {
        OffsetDateTime now = OffsetDateTime.now();
        jdbc.update("insert into sec_roles(id,tenant_id,code,name,built_in,created_at,updated_at) values(?,?,?,?,?,?,?)",
                id, tenantId, code, name, builtIn, now, now);
    }

    public void replaceMatrix(String tenantId, String roleId, List<String> permissions, List<Grant> grants) {
        jdbc.update("delete from sec_role_kb_grants where tenant_id=? and role_id=?", tenantId, roleId);
        jdbc.update("delete from sec_role_permissions where tenant_id=? and role_id=?", tenantId, roleId);
        permissions.forEach(permission -> jdbc.update(
                "insert into sec_role_permissions(tenant_id,role_id,permission_code) values(?,?,?)", tenantId, roleId, permission));
        grants.forEach(grant -> jdbc.update(
                "insert into sec_role_kb_grants(tenant_id,role_id,kb_id,permission_code) values(?,?,?,?)",
                tenantId, roleId, grant.scopeId(), grant.permission()));
        jdbc.update("update sec_roles set updated_at=? where tenant_id=? and id=?", OffsetDateTime.now(), tenantId, roleId);
    }

    public boolean permissionExists(String tenantId, String code) {
        if ("*".equals(code)) return true;
        Integer count = jdbc.queryForObject("select count(*) from sec_permissions where tenant_id=? and code=?",
                Integer.class, tenantId, code);
        return count != null && count > 0;
    }

    private RoleView mapRole(java.sql.ResultSet rs) throws java.sql.SQLException {
        return new RoleView(rs.getString("id"), rs.getString("tenant_id"), rs.getString("code"), rs.getString("name"),
                rs.getBoolean("built_in"), List.of(), List.of(), 0,
                rs.getObject("created_at", OffsetDateTime.class), rs.getObject("updated_at", OffsetDateTime.class));
    }

    private RoleView enrich(RoleView role) {
        List<String> permissions = jdbc.query("select permission_code from sec_role_permissions where tenant_id=? and role_id=? "
                + "order by permission_code", (rs, n) -> rs.getString(1), role.tenantId(), role.id());
        List<Grant> grants = jdbc.query("select kb_id,permission_code from sec_role_kb_grants where tenant_id=? and role_id=? "
                        + "order by kb_id,permission_code", (rs, n) -> new Grant(rs.getString("permission_code"), "KB", rs.getString("kb_id")),
                role.tenantId(), role.id());
        Integer users = jdbc.queryForObject("select count(*) from sec_user_roles where tenant_id=? and role_id=?",
                Integer.class, role.tenantId(), role.id());
        return new RoleView(role.id(), role.tenantId(), role.code(), role.name(), role.builtIn(), permissions, grants,
                users == null ? 0 : users, role.createdAt(), role.updatedAt());
    }

    public record Grant(String permission, String scopeType, String scopeId) {
    }

    public record RoleView(String id, String tenantId, String code, String name, boolean builtIn,
                           List<String> permissions, List<Grant> grants, int userCount,
                           OffsetDateTime createdAt, OffsetDateTime updatedAt) {
    }
}
