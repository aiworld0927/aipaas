package com.aipaas.platform.audit;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

@Repository
public class AuditRepository {

    private final JdbcTemplate jdbc;

    public AuditRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private final RowMapper<AuditEntry> mapper = (rs, n) -> new AuditEntry(
            rs.getLong("sequence_no"), rs.getString("id"), rs.getString("tenant_id"), rs.getString("actor_id"),
            rs.getString("actor_name"), rs.getString("action"), rs.getString("object_type"), rs.getString("object_id"),
            rs.getString("result"), rs.getString("ip"), rs.getString("user_agent"), rs.getString("trace_id"),
            rs.getString("detail_json"), rs.getString("previous_hash"), rs.getString("entry_hash"),
            rs.getString("env"), rs.getBoolean("archive"), rs.getObject("created_at", OffsetDateTime.class));

    public Optional<AuditEntry> latest() {
        return jdbc.query("select * from sec_audit_logs order by sequence_no desc limit 1", mapper).stream().findFirst();
    }

    public void insert(AuditEntry e) {
        jdbc.update("insert into sec_audit_logs(id,tenant_id,actor_id,actor_name,action,object_type,object_id,result,ip,"
                        + "user_agent,trace_id,detail_json,previous_hash,entry_hash,env,archive,created_at) "
                        + "values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                e.id(), e.tenantId(), e.actorId(), e.actorName(), e.action(), e.objectType(), e.objectId(), e.result(),
                e.ip(), e.userAgent(), e.traceId(), e.detailJson(), e.previousHash(), e.entryHash(), e.env(),
                e.archive(), e.createdAt());
    }

    public List<AuditEntry> search(AuditQuery q, int limit, long offset) {
        SqlFilter filter = new SqlFilter(q);
        return jdbc.query("select * from sec_audit_logs" + filter.where() + " order by sequence_no desc limit ? offset ?",
                ps -> {
                    filter.bind(ps);
                    ps.setInt(filter.index(), limit);
                    ps.setLong(filter.index() + 1, offset);
                }, mapper);
    }

    public long count(AuditQuery q) {
        SqlFilter filter = new SqlFilter(q);
        return jdbc.query(con -> {
            var ps = con.prepareStatement("select count(*) from sec_audit_logs" + filter.where());
            filter.bind(ps);
            return ps;
        }, rs -> rs.next() ? rs.getLong(1) : 0L);
    }

    public List<AuditEntry> allForIntegrity() {
        return jdbc.query("select * from sec_audit_logs order by sequence_no", mapper);
    }

    public int deleteExpired(int retentionDays) {
        return jdbc.execute((org.springframework.jdbc.core.ConnectionCallback<Integer>) connection -> {
            boolean autoCommit = connection.getAutoCommit();
            connection.setAutoCommit(false);
            try (var set = connection.prepareStatement("select set_config('aipaas.audit_retention_days', ?, true)");
                 var delete = connection.prepareStatement("delete from sec_audit_logs where created_at < "
                         + "current_timestamp - (? || ' days')::interval")) {
                set.setString(1, Integer.toString(retentionDays));
                set.execute();
                delete.setInt(1, retentionDays);
                int count = delete.executeUpdate();
                connection.commit();
                return count;
            } catch (Exception ex) {
                connection.rollback();
                throw ex;
            } finally {
                connection.setAutoCommit(autoCommit);
            }
        });
    }

    private record SqlFilter(AuditQuery q) {
        String where() {
            return " where tenant_id=? and env=? and archive=false"
                    + optional(q.action(), " and action=?") + optional(q.actorId(), " and actor_id=?")
                    + optional(q.objectType(), " and object_type=?") + optional(q.objectId(), " and object_id=?")
                    + optional(q.result(), " and result=?") + (q.from() == null ? "" : " and created_at>=?")
                    + (q.to() == null ? "" : " and created_at<=?");
        }

        private String optional(String value, String sql) {
            return value == null || value.isBlank() ? "" : sql;
        }

        int bind(java.sql.PreparedStatement ps) throws java.sql.SQLException {
            int index = 1;
            ps.setString(index++, q.tenantId());
            ps.setString(index++, q.env());
            List<String> values = new ArrayList<>();
            for (String value : List.of(nullToEmpty(q.action()), nullToEmpty(q.actorId()), nullToEmpty(q.objectType()),
                    nullToEmpty(q.objectId()), nullToEmpty(q.result()))) {
                if (!value.isBlank()) {
                    ps.setString(index++, value);
                }
            }
            if (q.from() != null) {
                ps.setTimestamp(index++, Timestamp.from(q.from().toInstant()));
            }
            if (q.to() != null) {
                ps.setTimestamp(index++, Timestamp.from(q.to().toInstant()));
            }
            values.clear();
            return index - 1;
        }

        int index() {
            int count = 2;
            for (String value : List.of(nullToEmpty(q.action()), nullToEmpty(q.actorId()), nullToEmpty(q.objectType()),
                    nullToEmpty(q.objectId()), nullToEmpty(q.result()))) {
                if (!value.isBlank()) {
                    count++;
                }
            }
            if (q.from() != null) count++;
            if (q.to() != null) count++;
            return count + 1;
        }

        private static String nullToEmpty(String value) {
            return value == null ? "" : value;
        }
    }
}
