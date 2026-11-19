package com.aipaas.platform.ingest;

import com.aipaas.platform.crypto.EncryptionService;
import com.aipaas.platform.document.EncryptedObjectStore;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Map;
import java.util.concurrent.Executor;

/** #10 任务执行器：认领任务 → 解密文档 → 调用解析服务 → 落版本并推送状态。 */
@Component
public class IngestTaskRunner {

    private static final Logger log = LoggerFactory.getLogger(IngestTaskRunner.class);

    private final IngestTaskRepository repository;
    private final ParseResultRepository parseResults;
    private final DocumentParseClient parseClient;
    private final EncryptedObjectStore objectStore;
    private final EncryptionService encryption;
    private final IngestEventPublisher events;
    private final Executor executor;
    private final Path tempDir;

    public IngestTaskRunner(IngestTaskRepository repository, ParseResultRepository parseResults,
                            DocumentParseClient parseClient, EncryptedObjectStore objectStore,
                            EncryptionService encryption, IngestEventPublisher events,
                            @Qualifier("ingestExecutor") Executor executor,
                            @Value("${aipaas.storage.temp-path:/tmp/aipaas-uploads}") String tempPath) {
        this.repository = repository;
        this.parseResults = parseResults;
        this.parseClient = parseClient;
        this.objectStore = objectStore;
        this.encryption = encryption;
        this.events = events;
        this.executor = executor;
        this.tempDir = Path.of(tempPath).resolve("parse");
    }

    public void run(String taskId) {
        IngestTaskRepository.TaskRow task = repository.findById(taskId).orElse(null);
        if (task == null) {
            log.warn("任务不存在，跳过执行 taskId={}", taskId);
            return;
        }
        if (!repository.claim(taskId, OffsetDateTime.now(ZoneOffset.UTC))) {
            log.debug("任务已被处理或状态非 QUEUED，跳过 taskId={}", taskId);
            return;
        }
        IngestTaskRepository.TaskRow running = repository.findById(taskId).orElse(task);
        repository.markDocument(task.tenantId(), task.documentId(), "PARSING", null, null,
                OffsetDateTime.now(ZoneOffset.UTC));
        events.publish(taskId, "status", statusEvent(running));

        Path plaintext = null;
        try {
            IngestTaskRepository.DocumentRow document = repository
                    .findDocument(task.tenantId(), task.documentId())
                    .orElseThrow(() -> new IllegalStateException("关联文档不存在: " + task.documentId()));
            String filename = encryption.decrypt(document.filenameCiphertext(), document.id() + ":filename");
            String standardNo = document.standardNoCiphertext() == null
                    ? null : encryption.decrypt(document.standardNoCiphertext(), document.id() + ":standardNo");
            plaintext = createPlaintext(document);

            DocumentParseClient.ParseOutcome outcome = parseClient.parse(new DocumentParseClient.ParseRequest(
                    document.id(), document.tenantId(), filename, document.format(), standardNo, plaintext));
            OffsetDateTime finished = OffsetDateTime.now(ZoneOffset.UTC);
            parseResults.save(task.tenantId(), task.documentId(), taskId, outcome.parser(), outcome.success(),
                    outcome.failureCode(), outcome.pages(), outcome.chunkCount(), outcome.artifactKey(),
                    outcome.resultJson(), finished);

            if (outcome.success()) {
                repository.markSucceeded(taskId, finished);
                repository.markDocumentReady(task.tenantId(), task.documentId(), outcome.chunkCount(), finished);
                IngestTaskRepository.TaskRow done = repository.findById(taskId).orElse(running);
                events.publish(taskId, "status", statusEvent(done));
                events.complete(taskId, "done", Map.of("taskId", taskId, "status", "SUCCEEDED",
                        "version", outcome.version(), "warnings", outcome.warningCount()));
                return;
            }
            String code = outcome.failureCode() == null ? "BIZ.PARSE_FAILED" : outcome.failureCode();
            String message = outcome.failureMessage() == null ? "解析失败" : outcome.failureMessage();
            repository.markFailed(taskId, code, message, outcome.retryable(), finished);
            repository.markDocument(task.tenantId(), task.documentId(), "FAILED", code, message, finished);
            IngestTaskRepository.TaskRow failed = repository.findById(taskId).orElse(running);
            events.publish(taskId, "status", statusEvent(failed));
            events.complete(taskId, "done", Map.of("taskId", taskId, "status", "FAILED", "code", code));
        } catch (ParseClientException ex) {
            log.warn("解析服务不可用 taskId={}: {}", taskId, ex.getMessage());
            retryOrFail(taskId, "SYS.DEPENDENCY_UNAVAILABLE", ex.getMessage());
        } catch (Exception ex) {
            log.error("任务执行异常 taskId={}", taskId, ex);
            retryOrFail(taskId, "SYS.INTERNAL", ex.getMessage() == null ? ex.getClass().getSimpleName()
                    : ex.getMessage());
        } finally {
            if (plaintext != null) {
                try {
                    Files.deleteIfExists(plaintext);
                } catch (IOException ex) {
                    log.warn("临时明文清理失败 path={}: {}", plaintext, ex.getMessage());
                }
            }
        }
    }

    /** 传输层失败：未超过 maxRetries 时自动重排，否则标记失败；重排复用同一任务行。 */
    private void retryOrFail(String taskId, String code, String message) {
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        IngestTaskRepository.TaskRow task = repository.findById(taskId).orElse(null);
        if (task == null) {
            return;
        }
        if (repository.requeueAuto(taskId, now)) {
            repository.markDocument(task.tenantId(), task.documentId(), "QUEUED", null, null, now);
            IngestTaskRepository.TaskRow queued = repository.findById(taskId).orElse(task);
            events.publish(taskId, "status", statusEvent(queued));
            executor.execute(() -> run(taskId));
            return;
        }
        repository.markFailed(taskId, code, message, true, now);
        repository.markDocument(task.tenantId(), task.documentId(), "FAILED", code, message, now);
        IngestTaskRepository.TaskRow failed = repository.findById(taskId).orElse(task);
        events.publish(taskId, "status", statusEvent(failed));
        events.complete(taskId, "done", Map.of("taskId", taskId, "status", "FAILED", "code", code));
    }

    private Path createPlaintext(IngestTaskRepository.DocumentRow document) throws IOException {
        Files.createDirectories(tempDir);
        Path plaintext = Files.createTempFile(tempDir, "doc-", ".bin");
        try {
            Files.setPosixFilePermissions(plaintext, PosixFilePermissions.fromString("rw-------"));
        } catch (UnsupportedOperationException ignored) {
            // 非 POSIX 文件系统（如 Windows）沿用默认权限
        }
        try (InputStream ciphertext = objectStore.open(document.objectKey())) {
            encryption.decryptToFile(ciphertext, plaintext, document.id() + ":object");
        }
        return plaintext;
    }

    private Map<String, Object> statusEvent(IngestTaskRepository.TaskRow task) {
        Map<String, Object> event = new java.util.LinkedHashMap<>();
        event.put("taskId", task.id());
        event.put("status", task.status());
        event.put("stage", task.stage());
        event.put("progress", task.progress());
        event.put("retryCount", task.retryCount());
        event.put("at", OffsetDateTime.now(ZoneOffset.UTC));
        if (task.errorCode() != null) {
            event.put("code", task.errorCode());
            event.put("message", task.errorMessage());
        }
        return event;
    }
}
