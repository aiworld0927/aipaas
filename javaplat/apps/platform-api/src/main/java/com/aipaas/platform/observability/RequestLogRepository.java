package com.aipaas.platform.observability;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;

@Repository
public class RequestLogRepository {

    private final JdbcTemplate jdbc;

    public RequestLogRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private final RowMapper<RequestLogEntry> mapper = (rs, n) -> new RequestLogEntry(
            rs.getString("id"), rs.getString("tenant_id"), rs.getString("user_id"), rs.getString("method"),
            rs.getString("path"), rs.getInt("status"), rs.getString("outcome"), rs.getInt("duration_ms"),
            rs.getString("service"), rs.getString("trace_id"), rs.getString("client_ip_hash"),
            rs.getObject("created_at", OffsetDateTime.class));

    public void insert(RequestLogEntry entry) {
        jdbc.update("insert into request_logs(id,tenant_id,user_id,method,path,status,outcome,duration_ms,service,"
                        + "trace_id,client_ip_hash,created_at) values(?,?,?,?,?,?,?,?,?,?,?,?)",
                entry.requestId(), entry.tenantId(), entry.userId(), entry.method(), entry.path(), entry.status(),
                entry.outcome(), entry.latencyMs(), entry.service(), entry.traceId(), entry.clientIpHash(),
                entry.occurredAt());
    }

    public List<RequestLogEntry> search(RequestLogQuery query, int limit, long offset) {
        SqlFilter filter = new SqlFilter(query);
        return jdbc.query("select * from request_logs" + filter.where()
                        + " order by created_at desc limit ? offset ?",
                ps -> {
                    filter.bind(ps);
                    ps.setInt(filter.index(), limit);
                    ps.setLong(filter.index() + 1, offset);
                }, mapper);
    }

    public long count(RequestLogQuery query) {
        SqlFilter filter = new SqlFilter(query);
        return jdbc.query(con -> {
            PreparedStatement ps = con.prepareStatement("select count(*) from request_logs" + filter.where());
            filter.bind(ps);
            return ps;
        }, rs -> rs.next() ? rs.getLong(1) : 0L);
    }

    public int deleteExpired(int retentionDays) {
        return jdbc.update("delete from request_logs where created_at < current_timestamp - (? || ' days')::interval",
                retentionDays);
    }

    private static final class SqlFilter {
        private final RequestLogQuery q;
        private final List<Object> values = new ArrayList<>();
        private int index = 1;

        SqlFilter(RequestLogQuery q) {
            this.q = q;
        }

        String where() {
            StringBuilder sql = new StringBuilder(" where tenant_id=?");
            values.add(q.tenantId());
            optional(q.traceId(), " and trace_id=?", sql);
            optional(q.userId(), " and user_id=?", sql);
            optional(q.path(), " and path=?", sql);
            if (q.status() != null) {
                sql.append(" and status=?");
                values.add(q.status());
            }
            if (q.from() != null) {
                sql.append(" and created_at>=?");
                values.add(q.from());
            }
            if (q.to() != null) {
                sql.append(" and created_at<=?");
                values.add(q.to());
            }
            return sql.toString();
        }

        private void optional(String value, String clause, StringBuilder sql) {
            if (value != null && !value.isBlank()) {
                sql.append(clause);
                values.add(value);
            }
        }

        void bind(PreparedStatement ps) throws SQLException {
            for (Object value : values) {
                ps.setObject(index++, value);
            }
        }

        int index() {
            return index;
        }
    }
}
