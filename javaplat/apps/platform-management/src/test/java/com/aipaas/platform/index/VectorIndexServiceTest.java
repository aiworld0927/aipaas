package com.aipaas.platform.index;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.fasterxml.jackson.databind.ObjectMapper;
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
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** #49 索引构建：别名热切换、失败保留旧索引、重试/回退、全量重建、耗时与资源可见。 */
class VectorIndexServiceTest {

    private JdbcTemplate jdbc;
    private VectorIndexRepository repository;
    private RecordingBackend backend;
    private VectorIndexService service;

    @BeforeEach
    void setUp() throws Exception {
        JdbcDataSource dataSource = new JdbcDataSource();
        dataSource.setURL("jdbc:h2:mem:idx_" + UUID.randomUUID().toString().replace("-", "")
                + ";MODE=PostgreSQL;DB_CLOSE_DELAY=-1;DATABASE_TO_LOWER=TRUE");
        try (Connection connection = dataSource.getConnection()) {
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V1__security_baseline.sql"));
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V3__knowledge_base_documents.sql"));
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V6__vector_index_builds.sql"));
        }
        jdbc = new JdbcTemplate(dataSource);
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        jdbc.update("insert into knowledge_bases(id,tenant_id,name,status,created_at,updated_at) values(?,?,?,?,?,?)",
                "kb_default", "default", "默认金融标准库", "ACTIVE", now, now);
        seedDocument("doc_a", 6);
        seedDocument("doc_b", 4);
        repository = new VectorIndexRepository(jdbc, new ObjectMapper());
        backend = new RecordingBackend();
        service = new VectorIndexService(repository, backend, Runnable::run, 1024, new ObjectMapper());
    }

    @Test
    void successfulBuildSwitchesAliasImmediatelyAndExposesMetrics() {
        Map<String, Object> view = service.create("default", "key-1", request(null, null));

        assertEquals("SUCCEEDED", view.get("status"));
        assertEquals(10, view.get("totalChunks"));
        assertEquals(10, view.get("indexedChunks"));
        assertEquals(100, view.get("progress"));
        assertNotNull(view.get("durationMs"));
        assertEquals("HNSW", view.get("indexType"));
        assertEquals("COSINE", view.get("metric"));
        assertEquals("auto", view.get("indexSlot"));
        assertEquals("online", view.get("resolvedSlot"));
        assertTrue(String.valueOf(view.get("collection")).endsWith("__online__v1"));
        assertTrue(String.valueOf(view.get("alias")).endsWith("__hnsw"));
        assertEquals(Map.of("M", 16, "efConstruction", 200), view.get("params"));
        assertEquals(view.get("collection"), backend.switchedCollection, "构建完成立即切换别名");
        assertEquals(12.5, ((Map<?, ?>) view.get("resourceUsage")).get("cpuPercent"));
        assertEquals("READY", jdbc.queryForObject(
                "select index_status from knowledge_bases where id='kb_default'", String.class));

        Map<String, Object> status = service.status("default", "kb_default");
        assertEquals("READY", status.get("indexStatus"));
        assertEquals(view.get("collection"), status.get("collection"));
        assertEquals(1024, status.get("dimension"));
        assertEquals(10L, status.get("chunkCount"));
        assertEquals(2, status.get("docCount"));
        assertEquals(view.get("id"), status.get("lastBuildId"));
    }

    @Test
    void failedBuildKeepsOldIndexAndRetryReusesBuild() {
        Map<String, Object> first = service.create("default", "key-1", request(null, null));
        backend.failBuild = true;
        Map<String, Object> failed = service.create("default", "key-2", request(null, true));

        assertEquals("FAILED", failed.get("status"));
        assertTrue(String.valueOf(failed.get("error")).contains("milvus down"));
        assertEquals(first.get("collection"), backend.switchedCollection, "失败不得切换别名，旧索引继续服务");
        assertEquals("FAILED", jdbc.queryForObject(
                "select index_status from knowledge_bases where id='kb_default'", String.class));

        backend.failBuild = false;
        Map<String, Object> retried = service.retry("default", String.valueOf(failed.get("id")));
        assertEquals("SUCCEEDED", retried.get("status"));
        assertEquals(2, retried.get("attempt"));
        assertEquals(failed.get("collection"), backend.switchedCollection);
        assertEquals("READY", jdbc.queryForObject(
                "select index_status from knowledge_bases where id='kb_default'", String.class));
        assertThrows(ApiException.class, () -> service.retry("default", String.valueOf(failed.get("id"))));
    }

    @Test
    void rollbackRestoresPreviousCollection() {
        Map<String, Object> first = service.create("default", "key-1", request(null, null));
        Map<String, Object> second = service.create("default", "key-2", request(null, true));
        assertEquals(first.get("collection"), second.get("previousCollection"));
        assertNotEquals(first.get("collection"), second.get("collection"));

        Map<String, Object> rolled = service.rollback("default", String.valueOf(second.get("id")));
        assertEquals(true, rolled.get("rolledBack"));
        assertEquals(first.get("collection"), backend.switchedCollection);
        assertEquals("READY", jdbc.queryForObject(
                "select index_status from knowledge_bases where id='kb_default'", String.class));

        Map<String, Object> status = service.status("default", "kb_default");
        assertEquals(first.get("collection"), status.get("collection"), "回退后状态指向旧集合");
        assertThrows(ApiException.class,
                () -> service.rollback("default", String.valueOf(first.get("id"))));
    }

    @Test
    void idempotencyAndValidationAndEmptyKb() {
        Map<String, Object> first = service.create("default", "same-key", request(null, null));
        Map<String, Object> duplicate = service.create("default", "same-key", request(null, null));
        assertEquals(first.get("id"), duplicate.get("id"));
        assertEquals(1, jdbc.queryForObject("select count(*) from vector_index_builds", Integer.class));

        ApiException badType = assertThrows(ApiException.class, () -> service.create("default", "key-x",
                new VectorIndexService.IndexBuildRequest("kb_default", false, "IVF_FLAT", null, null, null,
                        null, null, null, null, null)));
        assertEquals(ErrorCodes.PARAM_INVALID, badType.getErrorCode());

        jdbc.update("insert into knowledge_bases(id,tenant_id,name,status,created_at,updated_at) values(?,?,?,?,?,?)",
                "kb_empty", "default", "空库", "ACTIVE", OffsetDateTime.now(), OffsetDateTime.now());
        ApiException empty = assertThrows(ApiException.class,
                () -> service.create("default", "key-empty", request("kb_empty", null)));
        assertEquals(ErrorCodes.BIZ_INDEX_NOT_READY, empty.getErrorCode());

        assertThrows(ApiException.class, () -> service.status("default", "kb_missing"));
        assertThrows(ApiException.class, () -> service.get("default", "idx_missing"));
    }

    @Test
    void fullRebuildAllocatesNewVersionAndSlot() {
        Map<String, Object> first = service.create("default", "key-1", request(null, null));
        Map<String, Object> second = service.create("default", "key-2", request(null, true));
        assertTrue(String.valueOf(first.get("collection")).endsWith("__online__v1"));
        assertTrue(String.valueOf(second.get("collection")).endsWith("__offline__v2"));
        assertEquals("offline", second.get("resolvedSlot"));
        assertEquals(1, first.get("attempt"));
        assertFalse((Boolean) second.get("rolledBack"));
    }

    @Test
    void documentSubsetLimitsChunkCount() {
        Map<String, Object> view = service.create("default", "key-subset",
                new VectorIndexService.IndexBuildRequest("kb_default", false, null, null, null, null, null,
                        null, null, null, List.of("doc_b")));
        assertEquals(4, view.get("totalChunks"));
        assertEquals(4, view.get("indexedChunks"));
    }

    private VectorIndexService.IndexBuildRequest request(String kbId, Boolean fullRebuild) {
        return new VectorIndexService.IndexBuildRequest(kbId == null ? "kb_default" : kbId, fullRebuild,
                null, null, null, null, null, null, null, null, null);
    }

    private void seedDocument(String id, int chunks) {
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        jdbc.update("""
                insert into documents(id, tenant_id, kb_id, filename_ciphertext, object_key, format, size_bytes,
                    status, uploader_id, chunk_count, created_at, updated_at)
                values(?,?,?,?,?,?,?,?,?,?,?,?)
                """, id, "default", "kb_default", "enc", "objects/" + id, "pdf", 10L, "READY", "u1",
                chunks, now, now);
    }

    /** 可注入失败的索引引擎桩：记录别名切换目标。 */
    static class RecordingBackend implements VectorIndexBackend {
        boolean failBuild;
        String switchedCollection;

        @Override
        public BuildResult build(IndexSpec spec, String collection, int totalChunks, int dimension) {
            if (failBuild) {
                throw new RuntimeException("milvus down");
            }
            return new BuildResult(totalChunks, Map.of("cpuPercent", 12.5, "source", "test"));
        }

        @Override
        public void switchAlias(String alias, String collection) {
            this.switchedCollection = collection;
        }
    }
}
