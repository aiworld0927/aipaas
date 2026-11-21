package com.aipaas.platform.document;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.crypto.EncryptionService;
import com.aipaas.platform.crypto.KeyringService;
import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.core.io.ClassPathResource;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ScriptUtils;
import org.springframework.mock.web.MockMultipartFile;

import javax.crypto.spec.SecretKeySpec;
import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.sql.Connection;
import java.time.OffsetDateTime;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class DocumentServiceTest {

    private static final long MEGABYTE = 1024L * 1024L;

    @TempDir
    Path temp;

    private JdbcTemplate jdbc;
    private EncryptionService encryption;
    private LocalEncryptedObjectStore storage;
    private IndexCleanupService cleanup;
    private DocumentService service;

    @BeforeEach
    void setUp() throws Exception {
        JdbcDataSource dataSource = new JdbcDataSource();
        dataSource.setURL("jdbc:h2:mem:document_" + UUID.randomUUID().toString().replace("-", "")
                + ";MODE=PostgreSQL;DB_CLOSE_DELAY=-1;DATABASE_TO_LOWER=TRUE");
        try (Connection connection = dataSource.getConnection()) {
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V1__security_baseline.sql"));
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V3__knowledge_base_documents.sql"));
        }
        jdbc = new JdbcTemplate(dataSource);
        OffsetDateTime now = OffsetDateTime.now();
        jdbc.update("insert into knowledge_bases(id,tenant_id,name,status,created_at,updated_at) values(?,?,?,?,?,?)",
                "kb_default", "default", "默认库", "ACTIVE", now, now);

        KeyringService keyring = mock(KeyringService.class);
        when(keyring.activeKeyId()).thenReturn("k1");
        when(keyring.encryptionKey("k1")).thenReturn(new SecretKeySpec(new byte[32], "AES"));
        encryption = new EncryptionService(keyring);
        storage = new LocalEncryptedObjectStore(temp.resolve("objects").toString());
        cleanup = new IndexCleanupService(jdbc, storage, mock(VectorIndexGateway.class));
        jdbc.update("create table if not exists document_grants(id varchar(64) primary key, tenant_id varchar(64), "
                + "document_id varchar(64), subject_type varchar(16), subject_id varchar(64), permission varchar(16), "
                + "granted_by varchar(64), created_at timestamp with time zone)");
        DocumentRepository repository = new DocumentRepository(jdbc);
        DocumentGrantService grants = new DocumentGrantService(new DocumentGrantRepository(jdbc), repository);
        service = new DocumentService(repository, encryption, storage, cleanup,
                mock(ApplicationEventPublisher.class), grants, temp.resolve("upload-temp").toString());
    }

    @Test
    void uploadStoresOnlyCiphertextAndCreatesCorrectQueuedTask() throws Exception {
        String plaintext = "PDF-PLAINTEXT-MUST-NOT-BE-STORED";
        MockMultipartFile file = new MockMultipartFile("file", "金融标准.pdf", "application/pdf",
                plaintext.getBytes(StandardCharsets.UTF_8));
        SecurityPrincipal principal = principal();

        Map<String, Object> response = service.upload("kb_default", file, "JR/T 0088", true, principal);
        @SuppressWarnings("unchecked")
        Map<String, Object> document = (Map<String, Object>) response.get("document");
        String documentId = (String) document.get("id");
        Map<String, Object> row = jdbc.queryForMap("select * from documents where id=?", documentId);

        String encryptedName = (String) row.get("filename_ciphertext");
        String encryptedStandard = (String) row.get("standard_no_ciphertext");
        assertNotEquals("金融标准.pdf", encryptedName);
        assertNotEquals("JR/T 0088", encryptedStandard);
        assertEquals("金融标准.pdf", encryption.decrypt(encryptedName, documentId + ":filename"));
        assertEquals("JR/T 0088", encryption.decrypt(encryptedStandard, documentId + ":standardNo"));
        assertEquals(HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                .digest(plaintext.getBytes(StandardCharsets.UTF_8))), row.get("file_hash"));

        String objectKey = (String) row.get("object_key");
        byte[] stored = Files.readAllBytes(temp.resolve("objects").resolve(objectKey));
        assertFalse(new String(stored, StandardCharsets.UTF_8).contains(plaintext));
        assertTrue(Files.exists(temp.resolve("objects").resolve(objectKey)));

        Map<String, Object> task = jdbc.queryForMap("select * from ingest_tasks where document_id=?", documentId);
        assertEquals("QUEUED", task.get("status"));
        assertEquals("PARSE", task.get("stage"));
        assertEquals(0, ((Number) task.get("progress")).intValue());
    }

    @Test
    void uploadRejectsUnsupportedFormatBeforeCreatingObject() {
        MockMultipartFile file = new MockMultipartFile("file", "payload.exe", "application/octet-stream",
                "not executable content".getBytes(StandardCharsets.UTF_8));
        assertThrows(RuntimeException.class, () -> service.upload("kb_default", file, null, true, principal()));
        assertEquals(0, jdbc.queryForObject("select count(*) from documents", Integer.class));
    }

    @Test
    void hundredMegabyteChunkedUploadCompletesAndStoresOnlyCiphertext() throws Exception {
        long size = 100L * MEGABYTE;
        String fileName = "大型标准汇编.pdf";
        String fileHash = sha256OfGenerated(size);
        SecurityPrincipal principal = principal();

        Map<String, Object> init = service.initUpload("kb_default",
                new DocumentService.InitUploadRequest(fileName, size, fileHash, null, "JR/T 0001", true), principal);
        String uploadId = (String) init.get("uploadId");
        assertEquals(20, init.get("totalParts"));
        assertEquals(20, ((List<?>) init.get("missingParts")).size());

        for (int partNo = 1; partNo <= 20; partNo++) {
            long offset = (long) (partNo - 1) * DocumentService.PART_SIZE;
            int length = (int) Math.min(DocumentService.PART_SIZE, size - offset);
            byte[] part = generatedBytes(offset, length);
            Map<String, Object> partResult = service.putPart("kb_default", uploadId, partNo, offset, (long) length,
                    new ByteArrayInputStream(part), principal);
            assertEquals(partNo, ((List<?>) partResult.get("receivedParts")).size());
            assertEquals((long) partNo * DocumentService.PART_SIZE, partResult.get("offset"));
        }

        Map<String, Object> response = service.completeUpload("kb_default", uploadId, principal);
        @SuppressWarnings("unchecked")
        Map<String, Object> document = (Map<String, Object>) response.get("document");
        String documentId = (String) document.get("id");
        assertEquals(size, document.get("sizeBytes"));
        assertEquals("QUEUED", document.get("status"));
        assertEquals("PENDING", document.get("indexStatus"));

        Map<String, Object> row = jdbc.queryForMap("select * from documents where id=?", documentId);
        assertEquals(fileHash, row.get("file_hash"));
        assertEquals(size, ((Number) row.get("size_bytes")).longValue());
        assertEquals("大型标准汇编.pdf", encryption.decrypt((String) row.get("filename_ciphertext"),
                documentId + ":filename"));

        Path stored = temp.resolve("objects").resolve((String) row.get("object_key"));
        assertTrue(Files.exists(stored));
        assertTrue(Files.size(stored) > size);
        byte[] head = new byte[3];
        try (InputStream input = Files.newInputStream(stored)) {
            assertEquals(3, input.read(head));
        }
        assertArrayEquals("A1\n".getBytes(StandardCharsets.US_ASCII), head);

        assertEquals(1, jdbc.queryForObject("select count(*) from ingest_tasks where document_id=?",
                Integer.class, documentId));
        assertEquals("COMPLETED", jdbc.queryForObject("select status from document_upload_sessions where id=?",
                String.class, uploadId));
    }

    @Test
    void deleteSoftDeletesImmediatelyAndCleanupRemovesCiphertextObject() throws Exception {
        MockMultipartFile file = new MockMultipartFile("file", "待删除.txt", "text/plain",
                "delete-me".getBytes(StandardCharsets.UTF_8));
        SecurityPrincipal principal = principal();
        @SuppressWarnings("unchecked")
        Map<String, Object> document = (Map<String, Object>) service.upload("kb_default", file, null, true, principal)
                .get("document");
        String documentId = (String) document.get("id");
        String objectKey = jdbc.queryForObject("select object_key from documents where id=?", String.class, documentId);
        Path stored = temp.resolve("objects").resolve(objectKey);
        assertTrue(Files.exists(stored));

        Map<String, Object> deleted = service.delete(principal, documentId);

        assertEquals(true, deleted.get("deleted"));
        assertEquals(0, service.list(principal, "kb_default", null, null, 1, 20).get("total"));
        assertEquals("DELETED", jdbc.queryForObject("select index_status from documents where id=?",
                String.class, documentId));
        assertNotNull(jdbc.queryForObject("select deleted_at from documents where id=?", OffsetDateTime.class,
                documentId));

        assertEquals(1, cleanup.processOnce(10));
        assertFalse(Files.exists(stored));
        assertEquals("DONE", jdbc.queryForObject("select status from index_cleanup_tasks where document_id=?",
                String.class, documentId));
    }

    @Test
    void chunkedUploadRejectsMissingPermissionTargetBeforeObjectWrite() {
        assertThrows(ApiException.class, () -> service.initUpload("kb_missing",
                new DocumentService.InitUploadRequest("x.pdf", 10, "0".repeat(64), 1, null, true), principal()));
    }

    private static SecurityPrincipal principal() {
        return new SecurityPrincipal("u1", "default", "uploader", "上传员", "s1",
                Set.of("KB_UPLOADER"), Set.of("doc:upload"));
    }

    private static String sha256OfGenerated(long size) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[64 * 1024];
        for (long offset = 0; offset < size; offset += buffer.length) {
            int length = (int) Math.min(buffer.length, size - offset);
            fill(buffer, offset);
            digest.update(buffer, 0, length);
        }
        return HexFormat.of().formatHex(digest.digest());
    }

    private static byte[] generatedBytes(long offset, int length) {
        byte[] buffer = new byte[length];
        fill(buffer, offset);
        return buffer;
    }

    private static void fill(byte[] buffer, long offset) {
        for (int i = 0; i < buffer.length; i++) {
            buffer[i] = (byte) ((offset + i) * 31 + 7);
        }
    }
}
