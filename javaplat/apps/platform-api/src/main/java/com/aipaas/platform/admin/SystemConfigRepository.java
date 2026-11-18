package com.aipaas.platform.admin;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public class SystemConfigRepository {

    private final JdbcTemplate jdbc;

    public SystemConfigRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public Optional<Row> find(String tenantId) {
        List<Row> rows = jdbc.query("select tenant_id, config_json, updated_by, updated_at from system_configs "
                        + "where tenant_id=?", (rs, n) -> new Row(rs.getString("tenant_id"), rs.getString("config_json"),
                        rs.getString("updated_by"), rs.getObject("updated_at", OffsetDateTime.class)), tenantId);
        return rows.stream().findFirst();
    }

    public void save(String tenantId, String configJson, String updatedBy, OffsetDateTime now) {
        int updated = jdbc.update("update system_configs set config_json=?, updated_by=?, updated_at=? where tenant_id=?",
                configJson, updatedBy, now, tenantId);
        if (updated == 0) {
            jdbc.update("insert into system_configs(tenant_id, config_json, updated_by, updated_at) values(?,?,?,?)",
                    tenantId, configJson, updatedBy, now);
        }
    }

    public record Row(String tenantId, String configJson, String updatedBy, OffsetDateTime updatedAt) {
    }
}
