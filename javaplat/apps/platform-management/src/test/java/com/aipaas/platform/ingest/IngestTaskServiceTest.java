package com.aipaas.platform.ingest;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.crypto.EncryptionService;
import com.aipaas.platform.crypto.KeyringService;
import com.aipaas.platform.document.LocalEncryptedObjectStore;
import org.h2.jdbcx.JdbcDataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ScriptUtils;

import javax.crypto.spec.SecretKeySpec;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** #10 任务状态机：重试不产生重复数据、失败可重试、12 并发不阻塞、SSE 推送。 */
class IngestTaskServiceTest {

    @TempDir
    Path temp;

    private JdbcTemplate jdbc;
    private EncryptionService encryption;
    private LocalEncryptedObjectStore storage;
    private IngestTaskRepository repository;
    private ParseResultRepository parseResults;
    private IngestEventPublisher events;
    private StubParseClient client;
    private IngestTaskRunner runner;
    private IngestTaskService service;

    @BeforeEach
    void setUp() throws Exception {
        JdbcDataSource dataSource = new JdbcDataSource();
        dataSource.setURL("jdbc:h2:mem:ingest_" + UUID.randomUUID().toString().replace("-", "")
                + ";MODE=PostgreSQL;DB_CLOSE_DELAY=-1;DATABASE_TO_LOWER=TRUE");
        try (Connection connection = dataSource.getConnection()) {
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V1__security_baseline.sql"));
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V3__knowledge_base_documents.sql"));
            ScriptUtils.executeSqlScript(connection,
                    new ClassPathResource("db/migration/common/V5__ingest_task_state.sql"));
        }
        jdbc = new JdbcTemplate(dataSource);
        jdbc.update("insert into knowledge_bases(id,tenant_id,name,status,created_at,updated_at) values(?,?,?,?,?,?)",
                "kb_default", "default", "默认库", "ACTIVE", OffsetDateTime.now(), OffsetDateTime.now());

        KeyringService keyring = mock(KeyringService.class);
        when(keyring.activeKeyId()).thenReturn("k1");
        when(keyring.encryptionKey("k1")).thenReturn(new SecretKeySpec(new byte[32], "AES"));
        encryption = new EncryptionService(keyring);
        storage = new LocalEncryptedObjectStore(temp.resolve("objects").toString());
        repository = new IngestTaskRepository(jdbc);
        parseResults = new ParseResultRepository(jdbc);
        events = new IngestEventPublisher();
        client = new StubParseClient();
        runner = new IngestTaskRunner(repository, parseResults, client, storage, encryption, events,
                Runnable::run, temp.resolve("tmp").toString());
        service = new IngestTaskService(repository, events, new IngestTaskDispatcher(runner));
    }

    @Test
    void retryReusesTaskRowAndIncrementsArtifactVersionWithoutDuplicateChunks() throws Exception {
        seedDocument("doc_a");
        seedTask("tsk_a", "doc_a");
        client.outcome = failure("PARSE.PDF_SCANNED", "扫描件无可提取文本", false);

        runner.run("tsk_a");
        assertEquals("FAILED", task("tsk_a").status());
        assertEquals("PARSE.PDF_SCANNED", task("tsk_a").errorCode());
        assertFalse(Boolean.TRUE.equals(task("tsk_a").retryable()));

        client.outcome = success();
        Map<String, Object> view = service.retry("default", "tsk_a");

        assertEquals("QUEUED", view.get("status"));
        assertEquals(1, count("select count(*) from ingest_tasks where id='tsk_a'"));
        assertEquals(1, count("select count(*) from ingest_tasks"));
        assertEquals("SUCCEEDED", task("tsk_a").status());
        assertEquals(1, task("tsk_a").retryCount());
        assertEquals(List.of(1, 2), versions("doc_a"));
        assertEquals("READY", jdbc.queryForObject("select status from documents where id='doc_a'", String.class));
        assertEquals(4, jdbc.queryForObject("select chunk_count from documents where id='doc_a'", Integer.class));

        // 重复执行同一任务不会产生新的解析版本，保证重试幂等
        runner.run("tsk_a");
        assertEquals(List.of(1, 2), versions("doc_a"));
    }

    @Test
    void retryIsRejectedWhenTaskIsNotFailed() throws Exception {
        seedDocument("doc_b");
        seedTask("tsk_b", "doc_b");
        client.outcome = success();
        runner.run("tsk_b");

        ApiException error = assertThrows(ApiException.class, () -> service.retry("default", "tsk_b"));
        assertEquals("BIZ.TASK_NOT_RETRYABLE", error.getErrorCode());
        assertEquals(409, error.getStatus().value());
        assertEquals("SUCCEEDED", task("tsk_b").status());
    }

    @Test
    void transportFailureIsRetriedAtMostMaxRetriesAndNeverSwallowed() throws Exception {
        seedDocument("doc_c");
        seedTask("tsk_c", "doc_c");
        client.failure = new ParseClientException("connection refused");

        runner.run("tsk_c");

        IngestTaskRepository.TaskRow task = task("tsk_c");
        assertEquals("FAILED", task.status());
        assertEquals(2, task.retryCount());
        assertEquals("SYS.DEPENDENCY_UNAVAILABLE", task.errorCode());
        assertEquals(3, client.calls.get());
        assertEquals(1, count("select count(*) from ingest_tasks"));
    }

    @Test
    void twelveConcurrentTasksAllCompleteWithoutBlocking() throws Exception {
        client.delayMillis = 40;
        client.outcome = success();
        for (int i = 0; i < 12; i++) {
            seedDocument("doc_c" + i);
            seedTask("tsk_c" + i, "doc_c" + i);
        }

        long started = System.nanoTime();
        ExecutorService pool = Executors.newFixedThreadPool(12);
        List<Future<?>> futures = new ArrayList<>();
        for (int i = 0; i < 12; i++) {
            String taskId = "tsk_c" + i;
            futures.add(pool.submit(() -> runner.run(taskId)));
        }
        for (Future<?> future : futures) {
            future.get();
        }
        pool.shutdown();
        long elapsedMillis = (System.nanoTime() - started) / 1_000_000;

        assertEquals(12, count("select count(*) from ingest_tasks where status='SUCCEEDED'"));
        assertEquals(12, count("select count(*) from document_parse_results where success=true"));
        assertTrue(elapsedMillis < 3000, "12 个并发任务耗时过长：" + elapsedMillis + "ms");
        for (int i = 0; i < 12; i++) {
            assertEquals(List.of(1), versions("doc_c" + i));
        }
    }

    @Test
    void ssePublisherTracksSubscribersAndClosesOnTerminalEvent() {
        events.subscribe("tsk_sse", Map.of("taskId", "tsk_sse", "status", "QUEUED"));
        assertEquals(1, events.subscriberCount("tsk_sse"));

        events.publish("tsk_sse", "status", Map.of("taskId", "tsk_sse", "status", "RUNNING"));
        events.heartbeat();
        events.complete("tsk_sse", "done", Map.of("taskId", "tsk_sse", "status", "SUCCEEDED"));

        assertEquals(0, events.subscriberCount("tsk_sse"));
    }

    private void seedDocument(String documentId) throws Exception {
        String objectKey = "default/kb_default/" + documentId + ".a1";
        Path ciphertext = temp.resolve(documentId + ".a1");
        encryption.encryptToFile(new ByteArrayInputStream("# 样例".getBytes(StandardCharsets.UTF_8)), ciphertext,
                documentId + ":object");
        storage.put(objectKey, ciphertext);
        Files.deleteIfExists(ciphertext);
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        jdbc.update("insert into documents(id,tenant_id,kb_id,filename_ciphertext,object_key,format,size_bytes,"
                        + "status,uploader_id,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?,?)",
                documentId, "default", "kb_default", encryption.encrypt("样例.md", documentId + ":filename"),
                objectKey, "MD", 12L, "QUEUED", "usr_admin", now, now);
    }

    private void seedTask(String taskId, String documentId) {
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        jdbc.update("insert into ingest_tasks(id,tenant_id,document_id,kb_id,status,stage,progress,retry_count,"
                        + "max_retries,queued_at,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?,?,?)",
                taskId, "default", documentId, "kb_default", "QUEUED", "PARSE", 0, 0, 2, now, now, now);
    }

    private IngestTaskRepository.TaskRow task(String taskId) {
        return repository.findById(taskId).orElseThrow();
    }

    private List<Integer> versions(String documentId) {
        return jdbc.queryForList("select version from document_parse_results where document_id=? "
                + "order by version", Integer.class, documentId);
    }

    private int count(String sql) {
        Integer value = jdbc.queryForObject(sql, Integer.class);
        return value == null ? 0 : value;
    }

    private DocumentParseClient.ParseOutcome success() {
        return new DocumentParseClient.ParseOutcome(true, "markdown", null, null, false, 1, 1, 4, 1,
                "parse/default/doc/v1/parse-result.json", "{\"success\":true}");
    }

    private DocumentParseClient.ParseOutcome failure(String code, String message, boolean retryable) {
        return new DocumentParseClient.ParseOutcome(false, "pdf-native", code, message, retryable, 1, 2, 0, 2,
                "parse/default/doc/v1/parse-result.json", "{\"success\":false}");
    }

    /** 本地桩：模拟解析服务成功/业务失败/网络异常/耗时。 */
    private static final class StubParseClient implements DocumentParseClient {
        private final AtomicInteger calls = new AtomicInteger();
        private volatile ParseOutcome outcome;
        private volatile ParseClientException failure;
        private volatile long delayMillis;

        @Override
        public ParseOutcome parse(ParseRequest request) {
            calls.incrementAndGet();
            if (delayMillis > 0) {
                try {
                    Thread.sleep(delayMillis);
                } catch (InterruptedException ex) {
                    Thread.currentThread().interrupt();
                }
            }
            if (failure != null) {
                throw failure;
            }
            return outcome;
        }
    }
}
