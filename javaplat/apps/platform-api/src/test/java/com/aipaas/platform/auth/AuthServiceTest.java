package com.aipaas.platform.auth;

import com.aipaas.platform.audit.AuditService;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.config.BootstrapInitializer;
import com.aipaas.platform.crypto.EncryptionService;
import com.aipaas.platform.crypto.KeyringService;
import com.aipaas.platform.rbac.RoleRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ScriptUtils;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;

import java.sql.Connection;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class AuthServiceTest {

    private static final String PASSWORD = "S3cure!Vault#2026";

    private JdbcTemplate jdbc;
    private UserRepository users;
    private AuthService auth;
    private JwtService jwt;
    private AuditService audit;
    private PasswordEncoder encoder;

    @BeforeEach
    void setUp() throws Exception {
        JdbcDataSource dataSource = new JdbcDataSource();
        dataSource.setURL("jdbc:h2:mem:auth_" + UUID.randomUUID().toString().replace("-", "")
                + ";MODE=PostgreSQL;DB_CLOSE_DELAY=-1;DATABASE_TO_LOWER=TRUE");
        try (Connection connection = dataSource.getConnection()) {
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V1__security_baseline.sql"));
        }
        jdbc = new JdbcTemplate(dataSource);
        RoleRepository roles = new RoleRepository(jdbc);
        new BootstrapInitializer(jdbc, roles, mock(AuthService.class), "unused").run(null);

        users = new UserRepository(jdbc);
        encoder = new BCryptPasswordEncoder(4);
        audit = mock(AuditService.class);
        EncryptionService encryption = mock(EncryptionService.class);
        KeyringService keyring = mock(KeyringService.class);
        when(keyring.jwtSecret()).thenReturn("test-only-jwt-secret-with-at-least-32-bytes".getBytes());
        jwt = new JwtService(new ObjectMapper(), keyring, Duration.ofMinutes(30));
        auth = new AuthService(users, encoder, new PasswordPolicy(), jwt, audit, encryption,
                5, Duration.ofMinutes(15), Duration.ofDays(7));
        auth.createUser("default", "alice", "Alice", PASSWORD, null, List.of("KB_UPLOADER"));
    }

    @Test
    void passwordAndRefreshTokenAreNeverStoredInPlaintext() throws Exception {
        UserAccount stored = users.findByUsername("default", "alice").orElseThrow();
        assertNotEquals(PASSWORD, stored.passwordHash());
        assertTrue(stored.passwordHash().startsWith("$2"));
        assertTrue(encoder.matches(PASSWORD, stored.passwordHash()));

        Map<String, Object> response = auth.login("alice", PASSWORD, "default", "127.0.0.1", "test", "trace-login");
        String refreshToken = (String) response.get("refreshToken");
        String serialized = new ObjectMapper().writeValueAsString(response);
        assertFalse(serialized.contains(PASSWORD));

        Map<String, Object> tokenRow = jdbc.queryForMap("select token_hash from sec_refresh_tokens where user_id=?", stored.id());
        assertEquals(AuthService.hashToken(refreshToken), tokenRow.get("token_hash"));
        assertNotEquals(refreshToken, tokenRow.get("token_hash"));
        verify(audit).record(any(), eq("auth.login"), eq("user"), eq(stored.id()), eq("SUCCESS"),
                anyMap(), eq("127.0.0.1"), eq("test"), eq("trace-login"));
        assertAuditDetailsContainNoSecrets();
    }

    @Test
    void fiveFailuresLockAccountAndExpiredLockCanLoginAgain() {
        for (int attempt = 1; attempt <= 5; attempt++) {
            int currentAttempt = attempt;
            ApiException exception = assertThrows(ApiException.class,
                    () -> auth.login("alice", "wrong-password", "default", "ip", "ua", "bad-" + currentAttempt));
            assertEquals(attempt == 5 ? ErrorCodes.AUTH_ACCOUNT_LOCKED : ErrorCodes.AUTH_INVALID_CREDENTIALS,
                    exception.getErrorCode());
        }
        UserAccount locked = users.findByUsername("default", "alice").orElseThrow();
        assertEquals("LOCKED", locked.status());
        assertEquals(5, locked.failedAttempts());

        ApiException duringLock = assertThrows(ApiException.class,
                () -> auth.login("alice", PASSWORD, "default", "ip", "ua", "during-lock"));
        assertEquals(ErrorCodes.AUTH_ACCOUNT_LOCKED, duringLock.getErrorCode());

        jdbc.update("update sec_users set locked_until=? where id=?",
                OffsetDateTime.now(ZoneOffset.UTC).minusSeconds(1), locked.id());
        assertNotNull(auth.login("alice", PASSWORD, "default", "ip", "ua", "after-lock"));
        UserAccount unlocked = users.findByUsername("default", "alice").orElseThrow();
        assertEquals("ACTIVE", unlocked.status());
        assertEquals(0, unlocked.failedAttempts());
        verify(audit, times(6)).record(any(), eq("auth.login.failed"), eq("user"), eq(locked.id()),
                eq("FAILURE"), anyMap(), any(), any(), any());
        assertAuditDetailsContainNoSecrets();
    }

    @Test
    void unknownUserFailureIsAuditedWithoutEchoingPassword() {
        ApiException exception = assertThrows(ApiException.class,
                () -> auth.login("nobody", PASSWORD, "default", "ip", "ua", "unknown-user"));
        assertEquals(ErrorCodes.AUTH_INVALID_CREDENTIALS, exception.getErrorCode());
        verify(audit).record(any(), eq("auth.login.failed"), eq("user"), eq("nobody"), eq("FAILURE"),
                eq(Map.of("reason", "invalid_credentials")), eq("ip"), eq("ua"), eq("unknown-user"));
        assertAuditDetailsContainNoSecrets();
    }

    @Test
    void refreshRotationRejectsReplayAndRevokesReplacementToken() {
        Map<String, Object> login = auth.login("alice", PASSWORD, "default", "ip", "ua", "login");
        String original = (String) login.get("refreshToken");
        Map<String, Object> rotated = auth.refresh(original, "ip", "ua", "refresh");
        String replacement = (String) rotated.get("refreshToken");
        assertNotEquals(original, replacement);

        UserRepository.RefreshTokenRecord originalRecord = users.findRefreshToken(AuthService.hashToken(original)).orElseThrow();
        assertNotNull(originalRecord.revokedAt());
        assertNotNull(originalRecord.replacedBy());

        assertThrows(ApiException.class, () -> auth.refresh(original, "ip", "ua", "replay"));
        UserRepository.RefreshTokenRecord replacementRecord = users.findRefreshToken(AuthService.hashToken(replacement)).orElseThrow();
        assertNotNull(replacementRecord.revokedAt(), "旧令牌重放必须撤销同会话的新刷新令牌");
        verify(audit).record(any(), eq("auth.token.refresh.failed"), eq("session"), eq(originalRecord.sessionId()),
                eq("FAILURE"), eq(Map.of("reason", "refresh_token_reused")), eq("ip"), eq("ua"), eq("replay"));
        assertAuditDetailsContainNoSecrets();
    }

    @Test
    void logoutRevokesAccessTokenJtiAndSessionRefreshTokens() {
        Map<String, Object> login = auth.login("alice", PASSWORD, "default", "ip", "ua", "login");
        String accessToken = (String) login.get("accessToken");
        String refreshToken = (String) login.get("refreshToken");
        JwtService.AccessTokenClaims claims = jwt.verify(accessToken);
        SecurityPrincipal principal = new SecurityPrincipal(claims.userId(), claims.tenantId(), "alice", "Alice",
                claims.sessionId(), Set.of("KB_UPLOADER"), Set.of("doc:upload"));

        auth.logout(principal, claims.jti(), claims.expiresAt(), "ip", "ua", "logout");

        assertTrue(users.isAccessTokenRevoked(claims.jti()));
        assertNotNull(users.findRefreshToken(AuthService.hashToken(refreshToken)).orElseThrow().revokedAt());
        assertThrows(ApiException.class, () -> auth.refresh(refreshToken, "ip", "ua", "after-logout"));
        verify(audit).record(principal, "auth.logout", "session", claims.sessionId(), "SUCCESS", Map.of(),
                "ip", "ua", "logout");
        assertAuditDetailsContainNoSecrets();
    }

    private void assertAuditDetailsContainNoSecrets() {
        @SuppressWarnings("unchecked")
        ArgumentCaptor<Map<String, Object>> details = ArgumentCaptor.forClass(Map.class);
        verify(audit, atLeastOnce()).record(any(), anyString(), anyString(), any(), anyString(), details.capture(),
                any(), any(), any());
        for (Map<String, Object> detail : details.getAllValues()) {
            String serialized = detail.toString();
            assertFalse(serialized.contains(PASSWORD));
            assertFalse(serialized.matches(".*[A-Za-z0-9_-]{40,}\\.[A-Za-z0-9_-]{20,}\\..*"));
        }
    }
}
