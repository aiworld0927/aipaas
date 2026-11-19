package com.aipaas.platform.export;

import com.aipaas.platform.audit.AuditService;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.document.DocumentRepository;
import com.aipaas.platform.rbac.RbacService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.HttpStatus;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Executor;

/**
 * #406 条目化导出编排：
 * 创建任务（权限/KB 归属校验 + 审计）→ 异步执行（拉条款/简介 + 渲染 + 落对象存储）→ 状态查询 → 下载前再次鉴权。
 */
@Service
public class ExportService {

    private static final Logger log = LoggerFactory.getLogger(ExportService.class);
    private static final Set<String> TYPE_CLAUSES = Set.of("CLAUSES");
    private static final Set<String> TYPE_SUMMARY = Set.of("STANDARD_SUMMARY");
    private static final Map<String, Set<String>> TYPE_FORMATS = Map.of(
            "CLAUSES", Set.of("PDF", "XLSX"),
            "STANDARD_SUMMARY", Set.of("PDF", "DOCX"));
    private static final DateTimeFormatter STAMP = DateTimeFormatter.ofPattern("yyyyMMddHHmmss");

    private final ExportRepository repository;
    private final DocumentRepository documents;
    private final RbacService rbac;
    private final AuditService audit;
    private final ExportDataClient data;
    private final ExportRenderer renderer;
    private final ExportFileStore fileStore;
    private final Executor executor;
    private final Duration ttl;
    private final int maxRetries;
    private final Path tempDirectory;

    public ExportService(ExportRepository repository, DocumentRepository documents, RbacService rbac,
                         AuditService audit, ExportDataClient data, ExportRenderer renderer,
                         ExportFileStore fileStore, @Qualifier("ingestExecutor") Executor executor,
                         @Value("${aipaas.export.ttl:PT24H}") Duration ttl,
                         @Value("${aipaas.export.max-retries:2}") int maxRetries,
                         @Value("${aipaas.storage.temp-path:/tmp/aipaas-p0/uploads}") String tempDirectory) {
        this.repository = repository;
        this.documents = documents;
        this.rbac = rbac;
        this.audit = audit;
        this.data = data;
        this.renderer = renderer;
        this.fileStore = fileStore;
        this.executor = executor;
        this.ttl = ttl;
        this.maxRetries = Math.max(0, maxRetries);
        this.tempDirectory = Path.of(tempDirectory).toAbsolutePath().normalize();
    }

    /* ------------------------------------------------------------------ 创建 */

    public ExportView create(String kbId, CreateExportRequest request, SecurityPrincipal principal,
                             String idempotencyKey) {
        String documentId = request == null ? null : request.documentId();
        if (documentId == null || documentId.isBlank()) {
            throw new ApiException(ErrorCodes.PARAM_MISSING, HttpStatus.BAD_REQUEST, "documentId 不能为空");
        }
        if (kbId == null || kbId.isBlank()) {
            throw new ApiException(ErrorCodes.PARAM_MISSING, HttpStatus.BAD_REQUEST, "kbId 不能为空");
        }
        String type = normalize(request.type(), "CLAUSES");
        String format = normalize(request.format(), "PDF");
        if (!TYPE_CLAUSES.contains(type) && !TYPE_SUMMARY.contains(type)) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "不支持的导出类型: " + type);
        }
        if (!TYPE_FORMATS.get(type).contains(format)) {
            throw new ApiException(ErrorCodes.PARAM_UNSUPPORTED_FORMAT, HttpStatus.BAD_REQUEST,
                    type + " 不支持格式 " + format);
        }
        documents.findActive(principal.tenantId(), documentId)
                .filter(row -> kbId.equals(row.kbId()))
                .orElseThrow(() -> new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.NOT_FOUND,
                        "文档不存在或不属于该知识库"));
        // 先校验 KB 权限，再返回幂等结果，避免权限被回收后仍可读取任务元数据。
        requireAllowed(principal, "export:create", kbId, documentId, "export.create");

        ExportJob existing = repository.findByIdempotency(principal.tenantId(), principal.userId(), idempotencyKey)
                .orElse(null);
        if (existing != null) {
            return view(existing);
        }

        OffsetDateTime now = now();
        ExportJob job = new ExportJob("exp_" + UUID.randomUUID().toString().replace("-", ""),
                principal.tenantId(), kbId, documentId, type, format, "QUEUED", 0,
                "KB:" + kbId + "|DOC:" + documentId + "|" + ("CLAUSES".equals(type) ? "CLAUSES:ALL" : "SUMMARY:5"),
                0, 0, maxRetries, principal.userId(), idempotencyKey, null, null, 0, null,
                null, null, null, null, null, now, now);
        try {
            repository.insert(job);
        } catch (DuplicateKeyException race) {
            return repository.findByIdempotency(principal.tenantId(), principal.userId(), idempotencyKey)
                    .map(ExportService::view)
                    .orElseThrow(() -> race);
        }
        audit.record(principal, "export.create", "export", job.id(), "SUCCESS",
                Map.of("kbId", kbId, "documentId", documentId, "type", type, "format", format, "scope", job.scope()));
        submit(job.id());
        return view(job);
    }

    /* -------------------------------------------------------------- 查询/下载 */

    public ExportView get(String exportId, SecurityPrincipal principal) {
        ExportJob job = requireJob(exportId, principal);
        requireAllowed(principal, "export:read", job.kbId(), job.documentId(), "export.read");
        job = refreshExpiry(job);
        return view(job);
    }

    public Download download(String exportId, SecurityPrincipal principal) {
        ExportJob job = requireJob(exportId, principal);
        try {
            requireAllowed(principal, "export:download", job.kbId(), job.documentId(), "export.download");
        } catch (ApiException denied) {
            audit.record(principal, "export.download", "export", exportId, "DENIED",
                    Map.of("kbId", job.kbId(), "documentId", job.documentId(), "reason", "PERMISSION_DENIED"));
            throw denied;
        }
        job = refreshExpiry(job);
        if ("EXPIRED".equals(job.status())) {
            audit.record(principal, "export.download", "export", exportId, "DENIED",
                    Map.of("kbId", job.kbId(), "documentId", job.documentId(), "reason", "EXPIRED"));
            throw new ApiException(ErrorCodes.BIZ_CONFLICT, HttpStatus.CONFLICT, "导出文件已过期，请重新创建导出任务");
        }
        if (!"SUCCEEDED".equals(job.status()) || job.objectKey() == null) {
            throw new ApiException(ErrorCodes.BIZ_EXPORT_NOT_READY, HttpStatus.CONFLICT, "导出任务尚未完成");
        }
        byte[] content;
        try {
            content = fileStore.read(job.objectKey());
        } catch (RuntimeException ex) {
            log.error("export download failed id={}", exportId, ex);
            throw new ApiException(ErrorCodes.SYS_INTERNAL, HttpStatus.INTERNAL_SERVER_ERROR, "导出文件读取失败");
        }
        audit.record(principal, "export.download", "export", exportId, "SUCCESS",
                Map.of("kbId", job.kbId(), "documentId", job.documentId(), "clauseCount", job.clauseCount(),
                        "fileHash", String.valueOf(job.fileHash()), "sizeBytes", content.length, "scope", job.scope()));
        return new Download(job.fileName(), contentType(job.format()), content, job.fileHash());
    }

    public ExportView retry(String exportId, SecurityPrincipal principal) {
        ExportJob job = requireJob(exportId, principal);
        requireAllowed(principal, "export:create", job.kbId(), job.documentId(), "export.retry");
        if (!"FAILED".equals(job.status()) && !"EXPIRED".equals(job.status())) {
            throw new ApiException(ErrorCodes.BIZ_TASK_NOT_RETRYABLE, HttpStatus.CONFLICT, "仅失败或已过期的任务可重试");
        }
        if (repository.requeue(exportId, principal.userId(), now()) != 1) {
            throw new ApiException(ErrorCodes.BIZ_CONFLICT, HttpStatus.CONFLICT, "任务状态已变化，请刷新后重试");
        }
        audit.record(principal, "export.retry", "export", exportId, "SUCCESS",
                Map.of("kbId", job.kbId(), "documentId", job.documentId(), "previousStatus", job.status()));
        submit(exportId);
        return view(requireJob(exportId, principal));
    }

    /* ---------------------------------------------------------------- 执行器 */

    void submit(String exportId) {
        executor.execute(() -> run(exportId));
    }

    void run(String exportId) {
        OffsetDateTime now = now();
        ExportJob job = repository.findById(exportId);
        if (job == null || !repository.claim(exportId, now)) {
            return;
        }
        try {
            doExport(job);
            repository.progress(exportId, 100, now());
        } catch (RuntimeException ex) {
            handleFailure(job, ex);
        }
    }

    private void doExport(ExportJob job) {
        repository.progress(job.id(), 30, now());
        ExportDataClient.ClauseData clauses = data.clauses(job.documentId(), job.tenantId());
        if (clauses.clauseCount() <= 0) {
            throw new ExportDataClient.ExportDataException(ErrorCodes.BIZ_EXPORT_NOT_READY, "没有可导出的条款");
        }
        if (clauses.items().size() != clauses.clauseCount()) {
            throw new ExportDataClient.ExportDataException(ErrorCodes.BIZ_EXPORT_NOT_READY,
                    "条款数量不一致，拒绝导出以避免遗漏");
        }
        repository.progress(job.id(), 55, now());
        ExportDataClient.SummaryData summary = "STANDARD_SUMMARY".equals(job.exportType())
                ? data.summary(job.documentId(), job.tenantId()) : null;
        if (summary != null && (summary.elements() == null || summary.elements().size() != 5)) {
            throw new ExportDataClient.ExportDataException(ErrorCodes.BIZ_EXPORT_NOT_READY,
                    "标准简介五要素不完整，拒绝导出");
        }
        byte[] content;
        int clauseCount;
        if (summary != null) {
            clauseCount = summary.elements().size();
            content = "DOCX".equals(job.format())
                    ? renderer.summaryDocx(summary)
                    : renderer.summaryPdf(summary);
        } else {
            clauseCount = clauses.items().size();
            content = "XLSX".equals(job.format())
                    ? renderer.clausesXlsx(clauses)
                    : renderer.clausesPdf("条款清单 " + job.documentId(), clauses);
        }
        repository.progress(job.id(), 80, now());
        String hash = sha256(content);
        String fileName = fileName(summary, job);
        String objectKey = job.tenantId() + "/exports/" + job.id() + "." + job.format().toLowerCase(Locale.ROOT) + ".a1";
        Path temp = null;
        try {
            Files.createDirectories(tempDirectory);
            temp = Files.createTempFile(tempDirectory, job.id() + "-", "." + job.format().toLowerCase(Locale.ROOT));
            Files.write(temp, content);
            fileStore.put(objectKey, temp);
        } catch (IOException ex) {
            throw new UncheckedIOException("导出文件暂存失败", ex);
        } finally {
            deleteQuietly(temp);
        }
        OffsetDateTime now = now();
        repository.succeed(job.id(), clauseCount, objectKey, fileName, content.length, hash,
                now.plus(ttl), now);
        audit.record(systemActor(job), "export.finish", "export", job.id(), "SUCCESS",
                Map.of("kbId", job.kbId(), "documentId", job.documentId(), "clauseCount", clauseCount,
                        "fileHash", hash, "sizeBytes", content.length, "expiresAt", now.plus(ttl).toString()));
    }

    private void handleFailure(ExportJob job, RuntimeException ex) {
        String code = ex instanceof ExportDataClient.ExportDataException dataError
                ? dataError.code() : ErrorCodes.SYS_INTERNAL;
        String message = abbreviate(ex.getMessage(), 500);
        if (job.retryCount() < job.maxRetries()) {
            repository.retryLater(job.id(), code, message, now());
            audit.record(systemActor(job), "export.retry", "export", job.id(), "FAILED",
                    Map.of("kbId", job.kbId(), "documentId", job.documentId(), "errorCode", code,
                            "retryCount", job.retryCount() + 1));
            submit(job.id());
            return;
        }
        repository.fail(job.id(), code, message, now());
        audit.record(systemActor(job), "export.finish", "export", job.id(), "FAILED",
                Map.of("kbId", job.kbId(), "documentId", job.documentId(), "errorCode", code));
    }

    /** 到期清理：删除对象存储文件并置 EXPIRED，避免过期文件继续可下载。 */
    @Scheduled(fixedDelayString = "${aipaas.export.cleanup-interval-ms:600000}")
    public void purgeExpired() {
        for (ExportJob job : repository.expiredBefore(now(), 100)) {
            if (job.objectKey() != null) {
                try {
                    fileStore.delete(job.objectKey());
                } catch (RuntimeException ex) {
                    log.warn("export object delete failed id={}", job.id(), ex);
                }
            }
            repository.markExpired(job.id(), now());
        }
    }

    /* ---------------------------------------------------------------- 辅助 */

    private ExportJob requireJob(String exportId, SecurityPrincipal principal) {
        return repository.find(principal.tenantId(), exportId)
                .orElseThrow(() -> new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.NOT_FOUND, "导出任务不存在"));
    }

    private ExportJob refreshExpiry(ExportJob job) {
        if ("SUCCEEDED".equals(job.status()) && job.expiresAt() != null && !job.expiresAt().isAfter(now())) {
            repository.markExpired(job.id(), now());
            return repository.find(job.tenantId(), job.id()).orElse(job);
        }
        return job;
    }

    private void requireAllowed(SecurityPrincipal principal, String permission, String kbId, String documentId,
                                String action) {
        if (rbac.allowed(principal, permission, kbId)) {
            return;
        }
        audit.record(principal, action, "knowledgeBase", kbId, "DENIED",
                Map.of("permission", permission, "documentId", documentId));
        throw new ApiException(ErrorCodes.PERM_EXPORT_FORBIDDEN, HttpStatus.FORBIDDEN, "无权导出该知识库内容");
    }

    private static ExportView view(ExportJob job) {
        return new ExportView(job.id(), job.kbId(), job.documentId(), job.exportType(), job.format(), job.status(),
                job.progress(), job.scope(), job.clauseCount(), job.retryCount(), job.maxRetries(),
                job.fileName(), job.sizeBytes(), job.fileHash(), job.errorCode(), job.errorMessage(),
                job.expiresAt(), job.createdAt(), job.finishedAt());
    }

    private static String fileName(ExportDataClient.SummaryData summary, ExportJob job) {
        String label = "STANDARD_SUMMARY".equals(job.exportType()) ? "标准简介" : "条款清单";
        String extension = job.format().toLowerCase(Locale.ROOT);
        String stamp = OffsetDateTime.now(ZoneOffset.UTC).format(STAMP);
        return safe(fileNameBase(summary, job)) + "_" + label + "_" + stamp + "." + extension;
    }

    private static String fileNameBase(ExportDataClient.SummaryData summary, ExportJob job) {
        String value = summary != null && summary.standardNo() != null && !summary.standardNo().isBlank()
                ? summary.standardNo() : job.documentId();
        return safe(value);
    }

    private static String safe(String value) {
        return value.replaceAll("[\\\\/:*?\"<>|\\s]+", "-");
    }

    private static String contentType(String format) {
        return switch (format) {
            case "PDF" -> "application/pdf";
            case "XLSX" -> "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
            case "DOCX" -> "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
            default -> "application/octet-stream";
        };
    }

    private static String sha256(byte[] content) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
        } catch (NoSuchAlgorithmException ex) {
            throw new IllegalStateException(ex);
        }
    }

    private static SecurityPrincipal systemActor(ExportJob job) {
        return new SecurityPrincipal(job.actorId(), job.tenantId(), job.actorId(), job.actorId(), "export",
                Set.of(), Set.of());
    }

    private static String normalize(String value, String fallback) {
        if (value == null || value.isBlank()) {
            return fallback;
        }
        return value.trim().toUpperCase(Locale.ROOT);
    }

    private static String abbreviate(String value, int limit) {
        String text = value == null ? "" : value;
        return text.length() <= limit ? text : text.substring(0, limit);
    }

    private static void deleteQuietly(Path path) {
        if (path == null) {
            return;
        }
        try {
            Files.deleteIfExists(path);
        } catch (IOException ignored) {
            // 清理失败不影响导出结果
        }
    }

    private static OffsetDateTime now() {
        return OffsetDateTime.now(ZoneOffset.UTC);
    }

    public record CreateExportRequest(String kbId, String documentId, String type, String format) {
    }

    public record ExportView(String id, String kbId, String documentId, String type, String format, String status,
                             int progress, String scope, int clauseCount, int retryCount, int maxRetries,
                             String fileName, long sizeBytes, String fileHash, String errorCode, String errorMessage,
                             OffsetDateTime expiresAt, OffsetDateTime createdAt, OffsetDateTime finishedAt) {
    }

    public record Download(String fileName, String contentType, byte[] content, String fileHash) {
    }
}
