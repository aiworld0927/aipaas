package com.aipaas.platform.knowledgebase;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.document.DocumentRepository;
import com.aipaas.platform.document.EncryptedObjectStore;
import com.aipaas.platform.document.IndexCleanupService;
import com.aipaas.platform.document.VectorIndexGateway;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ScriptUtils;

import java.sql.Connection;
import java.time.OffsetDateTime;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.mock;

class KnowledgeBaseServiceTest {

    private JdbcTemplate jdbc;
    private KnowledgeBaseService service;

    @BeforeEach
    void setUp() throws Exception {
        JdbcDataSource dataSource = new JdbcDataSource();
        dataSource.setURL("jdbc:h2:mem:kb_" + UUID.randomUUID().toString().replace("-", "")
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
        IndexCleanupService cleanup = new IndexCleanupService(jdbc, mock(EncryptedObjectStore.class),
                mock(VectorIndexGateway.class));
        service = new KnowledgeBaseService(new KnowledgeBaseRepository(jdbc), new DocumentRepository(jdbc), cleanup,
                new ObjectMapper());
    }

    @Test
    void createListRenameAndConfigureKnowledgeBase() {
        Map<String, Object> created = service.create("default", "u1", "监管标准库", "监管制度", "JR", "JR/T 0088",
                Map.of("chunkSize", 1024, "chunkOverlap", 128, "retrievalTopK", 8));

        String kbId = (String) created.get("id");
        assertEquals("监管标准库", created.get("name"));
        assertEquals("JR", created.get("domain"));
        assertEquals("JR/T 0088", created.get("standardSystem"));
        assertEquals("EMPTY", created.get("indexStatus"));
        assertEquals(0L, created.get("documentCount"));
        @SuppressWarnings("unchecked")
        Map<String, Object> config = (Map<String, Object>) created.get("config");
        assertEquals(1024, config.get("chunkSize"));
        assertEquals(128, config.get("chunkOverlap"));

        Map<String, Object> updated = service.update("default", kbId, "监管标准库V2", null, "PBOC", "JR/T 0088",
                Map.of("chunkSize", 800));
        assertEquals("监管标准库V2", updated.get("name"));
        assertEquals("PBOC", updated.get("domain"));
        assertEquals("监管制度", updated.get("description"));
        @SuppressWarnings("unchecked")
        Map<String, Object> updatedConfig = (Map<String, Object>) updated.get("config");
        assertEquals(800, updatedConfig.get("chunkSize"));

        Map<String, Object> page = service.list("default", 1, 20, "V2", "PBOC");
        assertEquals(1L, page.get("total"));
    }

    @Test
    void duplicateNameIsRejected() {
        service.create("default", "u1", "重复库", null, null, null, null);
        ApiException error = assertThrows(ApiException.class,
                () -> service.create("default", "u1", "重复库", null, null, null, null));
        assertEquals(ErrorCodes.BIZ_CONFLICT, error.getErrorCode());
    }

    @Test
    void deleteOnlyAffectsTargetKnowledgeBase() {
        Map<String, Object> first = service.create("default", "u1", "待删库", null, null, null, null);
        Map<String, Object> second = service.create("default", "u1", "保留库", null, null, null, null);
        String firstId = (String) first.get("id");
        String secondId = (String) second.get("id");
        insertDocument("doc_a", firstId);
        insertDocument("doc_b", secondId);

        Map<String, Object> deleted = service.delete("default", firstId);

        assertEquals(1, deleted.get("documents"));
        assertTrue(((java.util.List<?>) service.list("default", 1, 20, "待删库", null).get("items")).isEmpty());
        assertTrue(new DocumentRepository(jdbc).findActive("default", "doc_a").isEmpty());
        assertTrue(new DocumentRepository(jdbc).findActive("default", "doc_b").isPresent());
        assertEquals(1, jdbc.queryForObject("select count(*) from index_cleanup_tasks where document_id='doc_a'",
                Integer.class));
        assertEquals("ACTIVE", jdbc.queryForObject("select status from knowledge_bases where id=?", String.class,
                secondId));
    }

    private void insertDocument(String documentId, String kbId) {
        OffsetDateTime now = OffsetDateTime.now();
        jdbc.update("insert into documents(id,tenant_id,kb_id,filename_ciphertext,object_key,format,size_bytes,status,"
                        + "uploader_id,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?,?)",
                documentId, "default", kbId, "cipher", "objects/" + documentId, "TXT", 10, "QUEUED", "u1", now, now);
    }
}
