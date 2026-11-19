package com.aipaas.platform.observability;

import com.aipaas.platform.common.api.PageResult;
import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ScriptUtils;

import java.sql.Connection;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

/** #173 请求日志检索：按 TraceID/用户/状态码过滤，租户隔离。 */
class RequestLogServiceTest {

    private RequestLogService service;

    @BeforeEach
    void setUp() throws Exception {
        JdbcDataSource dataSource = new JdbcDataSource();
        dataSource.setURL("jdbc:h2:mem:reqlog_" + UUID.randomUUID().toString().replace("-", "")
                + ";MODE=PostgreSQL;DB_CLOSE_DELAY=-1;DATABASE_TO_LOWER=TRUE");
        try (Connection connection = dataSource.getConnection()) {
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V1__security_baseline.sql"));
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V8__request_logs.sql"));
        }
        JdbcTemplate jdbc = new JdbcTemplate(dataSource);
        service = new RequestLogService(new RequestLogRepository(jdbc), 30, false);
        service.record(entry("tr_1", "usr_1", "default", "/api/v1/chat", 200, "SUCCESS"));
        service.record(entry("tr_1", "usr_1", "default", "/api/v1/chat/stream", 500, "FAILURE"));
        service.record(entry("tr_2", "usr_2", "default", "/api/v1/kbs/kb-1/exports", 403, "DENIED"));
        service.record(entry("tr_3", "usr_9", "other", "/api/v1/chat", 200, "SUCCESS"));
    }

    @Test
    void filtersByTraceIdAndTenant() {
        PageResult<RequestLogEntry> page = service.search(
                new RequestLogQuery("default", "tr_1", null, null, null, null, null), 1, 20);
        assertEquals(2, page.total());
        assertTrue(page.items().stream().allMatch(item -> "tr_1".equals(item.traceId())));
    }

    @Test
    void filtersByStatusAndUser() {
        PageResult<RequestLogEntry> denied = service.search(
                new RequestLogQuery("default", null, null, null, 403, null, null), 1, 20);
        assertEquals(1, denied.total());
        assertEquals("usr_2", denied.items().get(0).userId());
        PageResult<RequestLogEntry> user = service.search(
                new RequestLogQuery("default", null, "usr_1", "/api/v1/chat", null, null, null), 1, 20);
        assertEquals(1, user.total());
    }

    @Test
    void tenantIsolationIsEnforced() {
        PageResult<RequestLogEntry> other = service.search(
                new RequestLogQuery("default", "tr_3", null, null, null, null, null), 1, 20);
        assertEquals(0, other.total());
    }

    private static RequestLogEntry entry(String traceId, String userId, String tenantId, String path,
                                         int status, String outcome) {
        return new RequestLogEntry(UUID.randomUUID().toString().replace("-", ""), tenantId, userId,
                "POST", path, status, outcome, 12, "platform-management", traceId, "abc",
                OffsetDateTime.now(ZoneOffset.UTC));
    }
}
