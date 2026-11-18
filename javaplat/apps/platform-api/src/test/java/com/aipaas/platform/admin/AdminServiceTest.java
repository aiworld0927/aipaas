package com.aipaas.platform.admin;

import com.aipaas.platform.auth.AuthService;
import com.aipaas.platform.auth.UserAccount;
import com.aipaas.platform.auth.UserRepository;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.api.PageResult;
import com.aipaas.platform.config.BootstrapInitializer;
import com.aipaas.platform.crypto.EncryptionService;
import com.aipaas.platform.crypto.KeyringService;
import com.aipaas.platform.rbac.RoleRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ScriptUtils;

import javax.crypto.spec.SecretKeySpec;
import java.sql.Connection;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class AdminServiceTest {

    private JdbcTemplate jdbc;
    private UserRepository users;
    private EncryptionService encryption;
    private AdminService admin;

    @BeforeEach
    void setUp() throws Exception {
        JdbcDataSource dataSource = new JdbcDataSource();
        dataSource.setURL("jdbc:h2:mem:admin_" + UUID.randomUUID().toString().replace("-", "")
                + ";MODE=PostgreSQL;DB_CLOSE_DELAY=-1;DATABASE_TO_LOWER=TRUE");
        try (Connection connection = dataSource.getConnection()) {
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V1__security_baseline.sql"));
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V3__knowledge_base_documents.sql"));
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V4__admin_system_config.sql"));
        }
        jdbc = new JdbcTemplate(dataSource);
        RoleRepository roles = new RoleRepository(jdbc);
        new BootstrapInitializer(jdbc, roles, mock(AuthService.class), "unused").run(null);

        KeyringService keyring = mock(KeyringService.class);
        when(keyring.activeKeyId()).thenReturn("k1");
        when(keyring.encryptionKey("k1")).thenReturn(new SecretKeySpec(new byte[32], "AES"));
        encryption = new EncryptionService(keyring);
        users = new UserRepository(jdbc);
        SystemConfigService systemConfig = new SystemConfigService(new SystemConfigRepository(jdbc), new ObjectMapper());
        admin = new AdminService(users, roles, encryption, systemConfig, jdbc, Duration.ofMinutes(15));
    }

    @Test
    void listUsersFiltersKeywordAndStatusWithoutExposingPasswordHash() {
        createUser("usr_alice", "alice", "Alice", "ACTIVE");
        createUser("usr_bob", "bob", "Bob", "DISABLED");

        PageResult<Map<String, Object>> result = admin.listUsers("default", 1, 20, "ali", "ACTIVE");

        assertEquals(1, result.total());
        Map<String, Object> user = result.items().getFirst();
        assertEquals("alice", user.get("username"));
        assertEquals("alice@example.com", user.get("email"));
        assertEquals(List.of("KB_UPLOADER"), user.get("roles"));
        assertFalse(user.containsKey("passwordHash"));
        assertFalse(user.containsKey("password_hash"));
        assertFalse(user.containsKey("emailCiphertext"));
    }

    @Test
    void updateUserChangesProfileStatusAndRolesButRejectsSelfDisable() {
        createUser("usr_alice", "alice", "Alice", "ACTIVE");
        createUser("usr_bob", "bob", "Bob", "ACTIVE");

        Map<String, Object> updated = admin.updateUser("default", "usr_alice", "usr_bob",
                "Bob V2", null, "DISABLED", List.of("KB_UPLOADER"));

        assertEquals("Bob V2", updated.get("displayName"));
        assertEquals("DISABLED", updated.get("status"));
        assertEquals(List.of("KB_UPLOADER"), updated.get("roles"));
        assertEquals("bob@example.com", updated.get("email"));

        ApiException selfDisable = assertThrows(ApiException.class, () -> admin.updateUser("default", "usr_alice",
                "usr_alice", null, null, "DISABLED", null));
        assertEquals(ErrorCodes.PARAM_INVALID, selfDisable.getErrorCode());

        ApiException unknownRole = assertThrows(ApiException.class, () -> admin.updateUser("default", "usr_alice",
                "usr_bob", null, null, null, List.of("NOT_A_ROLE")));
        assertEquals(ErrorCodes.PARAM_INVALID, unknownRole.getErrorCode());
    }

    @Test
    void systemConfigReturnsDefaultsAndPersistsUpdates() {
        Map<String, Object> defaults = admin.systemConfig("default");
        assertEquals("RAG", defaults.get("defaultCapability"));
        assertEquals(104857600L, ((Number) defaults.get("maxUploadSizeBytes")).longValue());
        assertTrue(((List<?>) defaults.get("supportedFormats")).contains("PDF"));

        Map<String, Object> updated = admin.updateSystemConfig("default", "usr_admin", "RAG",
                52428800L, 1500, false, 10);
        assertEquals(52428800L, ((Number) updated.get("maxUploadSizeBytes")).longValue());
        assertEquals(1500, updated.get("maxQueryLength"));
        assertEquals(false, updated.get("streamingEnabled"));
        assertEquals(10, updated.get("retrievalTopK"));
        assertNotNull(updated.get("updatedAt"));

        Map<String, Object> reloaded = admin.systemConfig("default");
        assertEquals(52428800L, ((Number) reloaded.get("maxUploadSizeBytes")).longValue());
        assertEquals(10, reloaded.get("retrievalTopK"));
        assertNotNull(reloaded.get("updatedAt"));

        ApiException tooLarge = assertThrows(ApiException.class, () -> admin.updateSystemConfig("default",
                "usr_admin", null, 200L * 1024L * 1024L, null, null, null));
        assertEquals(ErrorCodes.PARAM_INVALID, tooLarge.getErrorCode());
        ApiException empty = assertThrows(ApiException.class,
                () -> admin.updateSystemConfig("default", "usr_admin", null, null, null, null, null));
        assertEquals(ErrorCodes.PARAM_INVALID, empty.getErrorCode());
    }

    @Test
    void overviewAggregatesKnowledgeBasesDocumentsAndUsers() {
        createUser("usr_alice", "alice", "Alice", "ACTIVE");
        createUser("usr_bob", "bob", "Bob", "ACTIVE");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        jdbc.update("insert into knowledge_bases(id,tenant_id,name,status,created_at,updated_at) values(?,?,?,?,?,?)",
                "kb_other", "default", "其它标准库", "ACTIVE", now, now);
        insertDocument("doc_1", "kb_default", 10, null);
        insertDocument("doc_2", "kb_other", 5, null);
        insertDocument("doc_deleted", "kb_other", 7, now);

        Map<String, Object> overview = admin.overview("default");

        assertEquals(2L, overview.get("kbCount"));
        assertEquals(2L, overview.get("documentCount"));
        assertEquals(15L, overview.get("chunkCount"));
        assertEquals(2L, overview.get("userCount"));
    }

    private void createUser(String id, String username, String displayName, String status) {
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        users.create(new UserAccount(id, "default", username, displayName,
                encryption.encrypt(username + "@example.com", id + ":email"), "bcrypt-hash", status, 0, null, 1,
                null, now, now));
        users.assignRole("default", id, "KB_UPLOADER");
    }

    private void insertDocument(String id, String kbId, int chunkCount, OffsetDateTime deletedAt) {
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        jdbc.update("insert into documents(id,tenant_id,kb_id,filename_ciphertext,object_key,format,size_bytes,"
                        + "status,uploader_id,created_at,updated_at,index_status,chunk_count,deleted_at) "
                        + "values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                id, "default", kbId, "cipher", "objects/" + id, "PDF", 1024, "STORED", "usr_alice",
                now, now, "READY", chunkCount, deletedAt);
    }
}
