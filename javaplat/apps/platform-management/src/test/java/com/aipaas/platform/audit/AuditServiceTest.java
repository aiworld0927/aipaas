package com.aipaas.platform.audit;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.crypto.KeyringService;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ScriptUtils;

import java.sql.Connection;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.*;

class AuditServiceTest {

    private JdbcTemplate jdbc;
    private AuditRepository repository;
    private AuditService service;

    @BeforeEach
    void setUp() throws Exception {
        JdbcDataSource dataSource = new JdbcDataSource();
        dataSource.setURL("jdbc:h2:mem:audit_" + UUID.randomUUID().toString().replace("-", "")
                + ";MODE=PostgreSQL;DB_CLOSE_DELAY=-1;DATABASE_TO_LOWER=TRUE");
        try (Connection connection = dataSource.getConnection()) {
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V1__security_baseline.sql"));
        }
        jdbc = new JdbcTemplate(dataSource);
        repository = new AuditRepository(jdbc);
        KeyringService keyring = mock(KeyringService.class);
        when(keyring.auditSecret()).thenReturn("test-only-audit-hmac-secret-32-bytes".getBytes());
        service = new AuditService(repository, keyring, new ObjectMapper(), "prod");
    }

    @Test
    void appendRedactsSecretsTruncatesMetadataAndBuildsVerifiableChain() {
        SecurityPrincipal actor = actor("alice");
        service.record(actor, "auth.login", "user", "usr_1", "SUCCESS",
                Map.of("password", "plain-password", "nested", Map.of("accessToken", "raw-token"), "reason", "ok"),
                "127.0.0.1", "agent", "trace-1");
        service.record(actor, "document.upload", "document", "doc_1", "SUCCESS", Map.of("result", "stored"),
                "192.168.1.1", "agent", "trace-2");

        List<AuditEntry> entries = repository.allForIntegrity();
        assertEquals(2, entries.size());
        assertEquals(AuditService.GENESIS, entries.get(0).previousHash());
        assertEquals(entries.get(0).entryHash(), entries.get(1).previousHash());
        assertTrue(entries.stream().allMatch(entry -> entry.entryHash().matches("[0-9a-f]{64}")));
        assertFalse(entries.get(0).detailJson().contains("plain-password"));
        assertFalse(entries.get(0).detailJson().contains("raw-token"));
        assertTrue(entries.get(0).detailJson().contains("[REDACTED]"));
        assertDoesNotThrow(service::verifyIntegrity);

        String longIp = "1".repeat(100);
        String longAgent = "a".repeat(600);
        AuditEntry truncated = service.record(actor, "export.audit", "audit", null, "SUCCESS", Map.of(),
                longIp, longAgent, "trace-3");
        assertEquals(64, truncated.ip().length());
        assertEquals(512, truncated.userAgent().length());
        assertDoesNotThrow(service::verifyIntegrity);
    }

    @Test
    void searchUsesTenantActionActorObjectResultAndEnvironmentFilters() {
        SecurityPrincipal actor = actor("alice");
        service.record(actor, "auth.login", "user", "usr_1", "SUCCESS", Map.of(), "ip", "ua", "trace-1");
        service.record(actor, "document.delete", "document", "doc_1", "SUCCESS", Map.of(), "ip", "ua", "trace-2");
        service.record(actor, "document.delete", "document", "doc_2", "FAILURE", Map.of(), "ip", "ua", "trace-3");

        AuditQuery query = new AuditQuery("default", "prod", "document.delete", actor.userId(),
                "document", "doc_1", "SUCCESS", null, null);
        AuditService.PageData result = service.search(query, 1, 20);
        assertEquals(1, result.total());
        assertEquals("doc_1", result.items().getFirst().get("objectId"));
        assertEquals(0, service.search(new AuditQuery("other", "prod", null, null, null, null,
                null, null, null), 1, 20).total());
        assertEquals(0, service.search(new AuditQuery("default", "dev", null, null, null, null,
                null, null, null), 1, 20).total());
    }

    @Test
    void csvExportPreventsSpreadsheetFormulaInjection() {
        SecurityPrincipal actor = new SecurityPrincipal("usr_csv", "default", "=1+1", "csv", "session",
                Set.of(), Set.of());
        service.record(actor, "export.audit", "audit", "+cmd", "SUCCESS", Map.of("safe", "value"),
                "ip", "ua", "trace-csv");

        String csv = service.exportCsv(new AuditQuery("default", "prod", null, null, null, null,
                null, null, null));

        assertTrue(csv.startsWith("\uFEFF"));
        assertTrue(csv.contains("\"'=1+1\""));
        assertTrue(csv.contains("\"'+cmd\""));
        assertTrue(csv.contains("export.audit"));
    }

    @Test
    void anyDatabaseTamperingIsDetectedByHashChainVerification() {
        AuditEntry entry = service.record(actor("alice"), "auth.login", "user", "usr_1", "SUCCESS",
                Map.of("reason", "ok"), "ip", "ua", "trace");
        jdbc.update("update sec_audit_logs set actor_name='tampered' where id=?", entry.id());

        assertThrows(IllegalStateException.class, service::verifyIntegrity);
    }

    @Test
    void retentionCannotBeBelow180DaysAndDisabledTaskDoesNotDelete() {
        AuditRepository mocked = mock(AuditRepository.class);
        assertThrows(IllegalArgumentException.class, () -> new AuditRetentionService(mocked, 179, true));
        AuditRetentionService disabled = new AuditRetentionService(mocked, 180, false);
        disabled.purgeExpired();
        verify(mocked, never()).deleteExpired(anyInt());
    }

    private static SecurityPrincipal actor(String username) {
        return new SecurityPrincipal("usr_1", "default", username, username, "session",
                Set.of("AUDITOR"), Set.of("audit:read"));
    }
}
