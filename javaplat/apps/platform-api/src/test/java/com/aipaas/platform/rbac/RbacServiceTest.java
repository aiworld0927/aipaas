package com.aipaas.platform.rbac;

import com.aipaas.platform.auth.AuthService;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.auth.UserAccount;
import com.aipaas.platform.auth.UserRepository;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.config.BootstrapInitializer;
import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ScriptUtils;

import java.sql.Connection;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.mock;

class RbacServiceTest {

    private JdbcTemplate jdbc;
    private UserRepository users;
    private RbacService rbac;
    private RoleRepository roles;

    @BeforeEach
    void setUp() throws Exception {
        JdbcDataSource dataSource = new JdbcDataSource();
        dataSource.setURL("jdbc:h2:mem:rbac_" + UUID.randomUUID().toString().replace("-", "")
                + ";MODE=PostgreSQL;DB_CLOSE_DELAY=-1;DATABASE_TO_LOWER=TRUE");
        try (Connection connection = dataSource.getConnection()) {
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V1__security_baseline.sql"));
        }
        jdbc = new JdbcTemplate(dataSource);
        roles = new RoleRepository(jdbc);
        new BootstrapInitializer(jdbc, roles, mock(AuthService.class), "unused").run(null);
        users = new UserRepository(jdbc);
        rbac = new RbacService(roles, users);
        jdbc.update("insert into knowledge_bases(id,tenant_id,name,status,created_at,updated_at) values(?,?,?,?,?,?)",
                "kb_other", "default", "其它知识库", "ACTIVE", OffsetDateTime.now(ZoneOffset.UTC),
                OffsetDateTime.now(ZoneOffset.UTC));
    }

    @Test
    void globalPermissionWithoutTargetKbGrantNeverPasses() {
        UserAccount user = createUser("uploader", "KB_UPLOADER");
        SecurityPrincipal stalePrincipal = principalWithClaims(user, "doc:upload", "doc:delete", "kb:manage");

        assertTrue(rbac.allowed(stalePrincipal, "doc:upload", "kb_default"));
        assertFalse(rbac.allowed(stalePrincipal, "doc:upload", "kb_other"));
        assertFalse(rbac.allowed(stalePrincipal, "doc:upload", "kb_missing"));
        assertFalse(rbac.allowed(stalePrincipal, "doc:delete", "kb_default"));
        assertFalse(rbac.allowed(stalePrincipal, "doc:delete", "kb_other"));
        assertFalse(rbac.allowed(stalePrincipal, "kb:manage", "kb_other"));
    }

    @Test
    void roleDowngradeAffectsSamePrincipalOnNextDecision() {
        UserAccount user = createUser("live", "KB_UPLOADER");
        SecurityPrincipal samePrincipal = principalWithClaims(user, "doc:upload");
        assertTrue(rbac.allowed(samePrincipal, "doc:upload", "kb_default"));

        long started = System.nanoTime();
        rbac.updateMatrix("default", "rol_kb_uploader", List.of("kb:read"), List.of());

        assertFalse(rbac.allowed(samePrincipal, "doc:upload", "kb_default"),
                "JWT 中的历史权限不得让降权后的请求继续通过");
        long elapsedMillis = (System.nanoTime() - started) / 1_000_000;
        assertTrue(elapsedMillis < 60_000, "权限变更必须在一分钟内生效");
    }

    @Test
    void kbManageSubstitutesUploadOnlyInsideGrantedKb() {
        UserAccount user = createUser("manager", "KB_ADMIN");
        rbac.updateMatrix("default", "rol_kb_admin", List.of("kb:manage"),
                List.of(new RoleRepository.Grant("kb:manage", "KB", "kb_default")));
        SecurityPrincipal principal = principalWithClaims(user);

        assertTrue(rbac.allowed(principal, "doc:upload", "kb_default"));
        assertTrue(rbac.allowed(principal, "kb:manage", "kb_default"));
        assertFalse(rbac.allowed(principal, "doc:upload", "kb_other"));
        assertFalse(rbac.allowed(principal, "doc:delete", "kb_other"));
    }

    @Test
    void platformAdminWildcardIsExplicitAndOtherRolesCannotSelfGrantIt() {
        UserAccount admin = createUser("root", "PLATFORM_ADMIN");
        assertTrue(rbac.allowed(principalWithClaims(admin), "doc:delete", "kb_other"));
        assertTrue(rbac.allowed(principalWithClaims(admin), "role:write", null));

        UserAccount uploader = createUser("ordinary", "KB_UPLOADER");
        assertFalse(rbac.allowed(principalWithClaims(uploader, "*"), "role:write", null),
                "调用方伪造的 JWT permissions claim 不得参与授权");
    }

    @Test
    void p0RejectsClauseScopeAndUnknownPermissions() {
        assertThrows(ApiException.class, () -> rbac.updateMatrix("default", "rol_kb_uploader",
                List.of("doc:upload"), List.of(new RoleRepository.Grant("doc:upload", "CLAUSE", "clause-1"))));
        assertThrows(ApiException.class, () -> rbac.updateMatrix("default", "rol_kb_uploader",
                List.of("not:exists"), List.of()));
    }

    private UserAccount createUser(String username, String role) {
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        UserAccount user = new UserAccount("usr_" + username, "default", username, username, null,
                "test-hash", "ACTIVE", 0, null, 1, null, now, now);
        users.create(user);
        users.assignRole("default", user.id(), role);
        return user;
    }

    private static SecurityPrincipal principalWithClaims(UserAccount user, String... permissions) {
        return new SecurityPrincipal(user.id(), user.tenantId(), user.username(), user.displayName(), "session",
                Set.of("STALE_ROLE_CLAIM"), Set.of(permissions));
    }
}
