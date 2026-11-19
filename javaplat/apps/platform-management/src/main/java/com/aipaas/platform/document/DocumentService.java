package com.aipaas.platform.document;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.crypto.EncryptionService;
import com.aipaas.platform.ingest.IngestTaskQueuedEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Service
public class DocumentService {

    private static final Logger log = LoggerFactory.getLogger(DocumentService.class);

    /** P0 单文件上限 100MB；P1 #253 在同一路径上扩展 GB 级分片。 */
    static final long MAX_BYTES = 100L * 1024 * 1024;
    static final long PART_SIZE = 5L * 1024 * 1024;
    private static final int MAX_PARTS = 20_000;
    private static final Set<String> FORMATS = Set.of("PDF", "DOC", "DOCX", "XLS", "XLSX", "PPT", "PPTX", "MD", "TXT");

    private final DocumentRepository repository;
    private final EncryptionService encryption;
    private final EncryptedObjectStore storage;
    private final IndexCleanupService cleanup;
    private final ApplicationEventPublisher events;
    private final Path tempDirectory;

    public DocumentService(DocumentRepository repository, EncryptionService encryption, EncryptedObjectStore storage,
                           IndexCleanupService cleanup, ApplicationEventPublisher events,
                           @Value("${aipaas.storage.temp-path:/tmp/aipaas-p0/uploads}") String tempDirectory) {
        this.repository = repository;
        this.encryption = encryption;
        this.storage = storage;
        this.cleanup = cleanup;
        this.events = events;
        this.tempDirectory = Path.of(tempDirectory).toAbsolutePath().normalize();
    }

    /* ------------------------------ 单次上传 ------------------------------ */

    @Transactional
    public Map<String, Object> upload(String kbId, MultipartFile file, String standardNo, boolean autoParse,
                                      SecurityPrincipal principal) {
        validateUpload(kbId, file.getOriginalFilename(), file.getSize(), principal.tenantId());
        String documentId = newId("doc_");
        String taskId = newId("tsk_");
        String fileName = safeFileName(file.getOriginalFilename());
        String format = format(fileName);
        String objectKey = principal.tenantId() + "/" + kbId + "/" + documentId + ".a1";
        Path ciphertext = null;
        try {
            Files.createDirectories(tempDirectory);
            ciphertext = Files.createTempFile(tempDirectory, documentId + "-", ".a1");
            restrict(ciphertext);
            String fileHash;
            try (InputStream raw = file.getInputStream();
                 DigestInputStream input = new DigestInputStream(raw, MessageDigest.getInstance("SHA-256"))) {
                encryption.encryptToFile(input, ciphertext, documentId + ":object");
                fileHash = HexFormat.of().formatHex(input.getMessageDigest().digest());
            } catch (java.security.NoSuchAlgorithmException ex) {
                throw new IllegalStateException("SHA-256 unavailable", ex);
            }
            storage.put(objectKey, ciphertext);
            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
            String encryptedName = encryption.encrypt(fileName, documentId + ":filename");
            String encryptedStandard = standardNo == null || standardNo.isBlank() ? null
                    : encryption.encrypt(standardNo, documentId + ":standardNo");
            repository.insertDocument(documentId, principal.tenantId(), kbId, encryptedName, encryptedStandard,
                    objectKey, format, file.getSize(), fileHash, principal.userId(), now);
            repository.insertTask(taskId, principal.tenantId(), documentId, kbId, autoParse ? "PARSE" : "UPLOAD", now);
            if (autoParse) {
                events.publishEvent(new IngestTaskQueuedEvent(taskId));
            }
            return uploadResponse(documentId, taskId, principal.tenantId(), kbId, fileName, format, file.getSize(),
                    standardNo, principal.userId(), autoParse ? "PARSE" : "UPLOAD", now, fileHash);
        } catch (IOException ex) {
            storage.delete(objectKey);
            throw new IllegalStateException("Secure upload failed", ex);
        } catch (RuntimeException ex) {
            storage.delete(objectKey);
            throw ex;
        } finally {
            deleteQuietly(ciphertext);
        }
    }

    /* ------------------------------ 分片上传 ------------------------------ */

    public Map<String, Object> initUpload(String kbId, InitUploadRequest request, SecurityPrincipal principal) {
        validateUpload(kbId, request.fileName(), request.sizeBytes(), principal.tenantId());
        String fileHash = normalizeHash(request.fileHash());
        int totalParts = request.totalParts() == null
                ? (int) ((request.sizeBytes() + PART_SIZE - 1) / PART_SIZE)
                : request.totalParts();
        long expectedParts = (request.sizeBytes() + PART_SIZE - 1) / PART_SIZE;
        if (totalParts < 1 || totalParts > MAX_PARTS || totalParts != expectedParts) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "分片数量与文件大小不匹配");
        }
        var existingDocument = repository.findActiveByHash(principal.tenantId(), kbId, fileHash);
        if (existingDocument.isPresent()) {
            DocumentRepository.DocumentRow document = existingDocument.get();
            String fileName = encryption.decrypt(document.filenameCiphertext(), document.id() + ":filename");
            return sessionView(null, kbId, fileName, fileHash, document.sizeBytes(), PART_SIZE,
                    totalParts, List.of(), "COMPLETED", document.id(), true, true);
        }
        var existing = repository.findOpenSessionByHash(principal.tenantId(), kbId, fileHash);
        if (existing.isPresent()) {
            DocumentRepository.UploadSessionRow session = existing.get();
            List<Integer> received = repository.parts(session.id()).stream()
                    .map(DocumentRepository.PartRow::partNo).toList();
            return sessionResponse(session, received, false);
        }
        String uploadId = "upl_" + UUID.randomUUID().toString().replace("-", "");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        String encryptedName = encryption.encrypt(safeFileName(request.fileName()), uploadId + ":filename");
        String encryptedStandard = request.standardNo() == null || request.standardNo().isBlank() ? null
                : encryption.encrypt(request.standardNo(), uploadId + ":standardNo");
        String objectKey = principal.tenantId() + "/" + kbId + "/uploads/" + uploadId + "/merged.a1";
        repository.insertSession(uploadId, principal.tenantId(), kbId, encryptedName, fileHash, request.sizeBytes(),
                PART_SIZE, totalParts, encryptedStandard, request.autoParse() == null || request.autoParse(),
                objectKey, principal.userId(), now);
        List<Integer> missing = new ArrayList<>();
        for (int part = 1; part <= totalParts; part++) {
            missing.add(part);
        }
        return sessionView(uploadId, kbId, request.fileName(), fileHash, request.sizeBytes(), PART_SIZE, totalParts,
                missing, "UPLOADING", null, false, false);
    }

    public Map<String, Object> putPart(String kbId, String uploadId, int partNo, Long offset, Long declaredSize,
                                       InputStream body, SecurityPrincipal principal) {
        DocumentRepository.UploadSessionRow session = requireSession(kbId, uploadId, principal.tenantId());
        if (!"UPLOADING".equals(session.status())) {
            throw new ApiException(ErrorCodes.BIZ_CONFLICT, HttpStatus.CONFLICT, "上传会话已完成");
        }
        if (partNo < 1 || partNo > session.totalParts()) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "分片序号越界");
        }
        long expectedOffset = (long) (partNo - 1) * session.partSize();
        if (offset != null && offset != expectedOffset) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "分片 offset 不匹配");
        }
        long expectedSize = partNo == session.totalParts()
                ? session.sizeBytes() - (long) (session.totalParts() - 1) * session.partSize()
                : session.partSize();
        if (declaredSize != null && declaredSize != expectedSize) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "分片大小不匹配");
        }
        String partKey = principal.tenantId() + "/" + kbId + "/uploads/" + uploadId + "/part-" + partNo + ".a1";
        Path ciphertext = null;
        try {
            Files.createDirectories(tempDirectory);
            ciphertext = Files.createTempFile(tempDirectory, uploadId + "-" + partNo + "-", ".a1");
            restrict(ciphertext);
            long written = encryption.encryptToFile(body, ciphertext, uploadId + ":part:" + partNo);
            if (written != expectedSize) {
                throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "分片实际大小不匹配");
            }
            storage.put(partKey, ciphertext);
            repository.savePart(uploadId, partNo, expectedOffset, expectedSize, partKey, null,
                    OffsetDateTime.now(ZoneOffset.UTC));
            List<Integer> received = repository.parts(uploadId).stream()
                    .map(DocumentRepository.PartRow::partNo).toList();
            return sessionResponse(session, received, false);
        } catch (IOException ex) {
            throw new IllegalStateException("Chunk upload failed", ex);
        } finally {
            deleteQuietly(ciphertext);
        }
    }

    public Map<String, Object> uploadStatus(String kbId, String uploadId, SecurityPrincipal principal) {
        DocumentRepository.UploadSessionRow session = requireSession(kbId, uploadId, principal.tenantId());
        List<Integer> received = repository.parts(uploadId).stream()
                .map(DocumentRepository.PartRow::partNo).toList();
        return sessionResponse(session, received, false);
    }

    @Transactional
    public Map<String, Object> completeUpload(String kbId, String uploadId, SecurityPrincipal principal) {
        DocumentRepository.UploadSessionRow session = requireSession(kbId, uploadId, principal.tenantId());
        if ("COMPLETED".equals(session.status())) {
            var existing = repository.findActiveByHash(principal.tenantId(), kbId, session.fileHash());
            if (existing.isPresent()) {
                return documentResponse(existing.get());
            }
            throw new ApiException(ErrorCodes.BIZ_CONFLICT, HttpStatus.CONFLICT, "上传会话已结束");
        }
        List<DocumentRepository.PartRow> parts = repository.parts(uploadId);
        List<Integer> missing = missingParts(session.totalParts(), parts);
        if (!missing.isEmpty()) {
            throw new ApiException(ErrorCodes.BIZ_CONFLICT, HttpStatus.CONFLICT,
                    "分片不完整，缺少 " + missing.size() + " 个分片");
        }
        String documentId = newId("doc_");
        String taskId = newId("tsk_");
        long expectedSize = session.sizeBytes();
        Path merged = null;
        try {
            Files.createDirectories(tempDirectory);
            merged = Files.createTempFile(tempDirectory, documentId + "-merged-", ".a1");
            restrict(merged);
            long plain;
            String mergedHash;
            try (EncryptionService.ObjectMerger merger = encryption.newMerger(merged, documentId + ":object")) {
                for (DocumentRepository.PartRow part : parts) {
                    try (InputStream ciphertext = storage.open(part.objectKey())) {
                        merger.append(ciphertext, uploadId + ":part:" + part.partNo());
                    }
                }
                EncryptionService.MergedObject result = merger.finish();
                plain = result.plainBytes();
                mergedHash = result.sha256();
            }
            if (plain != expectedSize) {
                throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "合并后大小与声明不一致");
            }
            if (!mergedHash.equalsIgnoreCase(session.fileHash())) {
                throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "文件哈希校验失败");
            }
            var duplicated = repository.findActiveByHash(principal.tenantId(), kbId, mergedHash);
            if (duplicated.isPresent()) {
                repository.completeSession(uploadId, OffsetDateTime.now(ZoneOffset.UTC));
                deleteParts(parts);
                return documentResponse(duplicated.get());
            }
            String fileName = encryption.decrypt(session.fileNameCiphertext(), uploadId + ":filename");
            String format = format(fileName);
            String documentName = encryption.encrypt(fileName, documentId + ":filename");
            String documentStandard = session.standardNoCiphertext() == null ? null
                    : encryption.encrypt(encryption.decrypt(session.standardNoCiphertext(), uploadId + ":standardNo"),
                    documentId + ":standardNo");
            storage.put(session.objectKey(), merged);
            try {
                OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
                repository.insertDocument(documentId, principal.tenantId(), kbId, documentName,
                        documentStandard, session.objectKey(), format, expectedSize, mergedHash,
                        principal.userId(), now);
                repository.insertTask(taskId, principal.tenantId(), documentId, kbId,
                        session.autoParse() ? "PARSE" : "UPLOAD", now);
                if (session.autoParse()) {
                    events.publishEvent(new IngestTaskQueuedEvent(taskId));
                }
                repository.completeSession(uploadId, now);
            } catch (RuntimeException ex) {
                storage.delete(session.objectKey());
                throw ex;
            }
            deleteParts(parts);
            return uploadResponse(documentId, taskId, principal.tenantId(), kbId, fileName, format, expectedSize,
                    null, principal.userId(), session.autoParse() ? "PARSE" : "UPLOAD",
                    OffsetDateTime.now(ZoneOffset.UTC), mergedHash);
        } catch (IOException ex) {
            throw new IllegalStateException("Chunk merge failed", ex);
        } finally {
            deleteQuietly(merged);
        }
    }

    private static List<Integer> missingParts(int totalParts, List<DocumentRepository.PartRow> parts) {
        Set<Integer> received = new java.util.HashSet<>();
        parts.forEach(part -> received.add(part.partNo()));
        List<Integer> missing = new ArrayList<>();
        for (int i = 1; i <= totalParts; i++) {
            if (!received.contains(i)) missing.add(i);
        }
        return missing;
    }

    private void deleteParts(List<DocumentRepository.PartRow> parts) {
        for (DocumentRepository.PartRow part : parts) {
            try {
                storage.delete(part.objectKey());
            } catch (RuntimeException ex) {
                log.warn("upload part cleanup deferred uploadPart={}", part.partNo());
            }
        }
    }

    /* ------------------------------ 文档管理 ------------------------------ */

    public Map<String, Object> list(String tenantId, String kbId, String status, String keyword, int page, int pageSize) {
        if (page < 1 || pageSize < 1 || pageSize > 100) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "分页参数不合法");
        }
        String normalizedKeyword = keyword == null ? null : keyword.trim().toLowerCase(Locale.ROOT);
        List<Map<String, Object>> matched = repository.listActive(tenantId, kbId, status).stream()
                .filter(row -> matches(row, normalizedKeyword))
                .map(this::documentResponse)
                .toList();
        int from = (int) Math.min((long) (page - 1) * pageSize, matched.size());
        int to = Math.min(from + pageSize, matched.size());
        return Map.of("items", matched.subList(from, to), "total", matched.size(), "page", page, "pageSize", pageSize);
    }

    public Map<String, Object> get(String tenantId, String documentId) {
        return documentResponse(requireDocument(tenantId, documentId));
    }

    @Transactional
    public Map<String, Object> delete(String tenantId, String documentId) {
        DocumentRepository.DocumentRow document = requireDocument(tenantId, documentId);
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        repository.softDelete(tenantId, documentId, now);
        cleanup.enqueue(tenantId, documentId, document.kbId(), document.objectKey());
        return Map.of("documentId", documentId, "deleted", true, "indexStatus", "DELETED");
    }

    private boolean matches(DocumentRepository.DocumentRow row, String keyword) {
        if (keyword == null || keyword.isBlank()) return true;
        String name = encryption.decrypt(row.filenameCiphertext(), row.id() + ":filename");
        return name.toLowerCase(Locale.ROOT).contains(keyword);
    }

    private Map<String, Object> documentResponse(DocumentRepository.DocumentRow row) {
        String fileName = encryption.decrypt(row.filenameCiphertext(), row.id() + ":filename");
        String standardNo = row.standardNoCiphertext() == null ? null
                : encryption.decrypt(row.standardNoCiphertext(), row.id() + ":standardNo");
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("id", row.id());
        view.put("tenantId", row.tenantId());
        view.put("kbId", row.kbId());
        view.put("fileName", fileName);
        view.put("format", row.format());
        view.put("sizeBytes", row.sizeBytes());
        view.put("status", row.status());
        view.put("indexStatus", row.indexStatus());
        view.put("chunkCount", row.chunkCount());
        view.put("standardNo", standardNo);
        view.put("uploaderId", row.uploaderId());
        view.put("source", "manual_upload");
        view.put("errorCode", row.errorCode());
        view.put("errorMessage", row.errorMessage());
        repository.latestTask(row.tenantId(), row.id()).ifPresent(task -> {
            view.put("lastTaskId", task.id());
            view.put("progress", task.progress());
        });
        view.put("createdAt", row.createdAt());
        view.put("updatedAt", row.updatedAt());
        return view;
    }

    private DocumentRepository.DocumentRow requireDocument(String tenantId, String documentId) {
        return repository.findActive(tenantId, documentId).orElseThrow(() ->
                new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.NOT_FOUND, "文档不存在"));
    }

    private DocumentRepository.UploadSessionRow requireSession(String kbId, String uploadId, String tenantId) {
        return repository.findSession(tenantId, kbId, uploadId).orElseThrow(() ->
                new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.NOT_FOUND, "上传会话不存在"));
    }

    /* ------------------------------ 响应与校验 ------------------------------ */

    private Map<String, Object> sessionResponse(DocumentRepository.UploadSessionRow session, List<Integer> received,
                                                boolean duplicate) {
        List<Integer> receivedSorted = received.stream().sorted().toList();
        List<Integer> missing = new ArrayList<>();
        Set<Integer> receivedSet = new java.util.HashSet<>(receivedSorted);
        for (int i = 1; i <= session.totalParts(); i++) {
            if (!receivedSet.contains(i)) missing.add(i);
        }
        long offset = 0;
        for (int partNo : receivedSorted) {
            if (partNo != (offset / session.partSize()) + 1) break;
            offset += partNo == session.totalParts()
                    ? session.sizeBytes() - (long) (session.totalParts() - 1) * session.partSize()
                    : session.partSize();
        }
        String fileName = encryption.decrypt(session.fileNameCiphertext(), session.id() + ":filename");
        return sessionView(session.id(), session.kbId(), fileName, session.fileHash(), session.sizeBytes(),
                session.partSize(), session.totalParts(), missing, session.status(), null, duplicate, session.autoParse(),
                receivedSorted, offset);
    }

    private Map<String, Object> sessionView(String uploadId, String kbId, String fileName, String fileHash, long size,
                                            long partSize, int totalParts, List<Integer> missing, String status,
                                            String documentId, boolean duplicate, boolean autoParse) {
        return sessionView(uploadId, kbId, fileName, fileHash, size, partSize, totalParts, missing, status, documentId,
                duplicate, autoParse, List.of(), 0);
    }

    private Map<String, Object> sessionView(String uploadId, String kbId, String fileName, String fileHash, long size,
                                            long partSize, int totalParts, List<Integer> missing, String status,
                                            String documentId, boolean duplicate, boolean autoParse,
                                            List<Integer> received, long offset) {
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("uploadId", uploadId);
        view.put("kbId", kbId);
        view.put("fileName", fileName);
        view.put("fileHash", fileHash);
        view.put("sizeBytes", size);
        view.put("partSize", partSize);
        view.put("totalParts", totalParts);
        view.put("receivedParts", received);
        view.put("missingParts", missing);
        view.put("offset", offset);
        view.put("status", status);
        view.put("autoParse", autoParse);
        view.put("duplicate", duplicate);
        if (documentId != null) view.put("documentId", documentId);
        return view;
    }

    private Map<String, Object> uploadResponse(String documentId, String taskId, String tenantId, String kbId,
                                               String fileName, String format, long size, String standardNo,
                                               String uploaderId, String stage, OffsetDateTime now, String fileHash) {
        Map<String, Object> document = new LinkedHashMap<>();
        document.put("id", documentId);
        document.put("tenantId", tenantId);
        document.put("kbId", kbId);
        document.put("fileName", fileName);
        document.put("format", format);
        document.put("sizeBytes", size);
        document.put("status", "QUEUED");
        document.put("indexStatus", "PENDING");
        document.put("chunkCount", 0);
        document.put("fileHash", fileHash);
        document.put("standardNo", standardNo);
        document.put("uploaderId", uploaderId);
        document.put("source", "manual_upload");
        document.put("lastTaskId", taskId);
        document.put("progress", 0);
        document.put("createdAt", now);
        document.put("updatedAt", now);
        Map<String, Object> task = new LinkedHashMap<>();
        task.put("id", taskId);
        task.put("tenantId", tenantId);
        task.put("documentId", documentId);
        task.put("kbId", kbId);
        task.put("status", "QUEUED");
        task.put("stage", stage);
        task.put("progress", 0);
        task.put("retryCount", 0);
        task.put("maxRetries", 2);
        task.put("queuedAt", now);
        task.put("createdAt", now);
        task.put("updatedAt", now);
        return Map.of("document", document, "ingestTask", task);
    }

    private void validateUpload(String kbId, String fileName, long size, String tenantId) {
        if (kbId == null || kbId.isBlank() || size <= 0) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "知识库或上传文件不合法");
        }
        if (size > MAX_BYTES) {
            throw new ApiException(ErrorCodes.PARAM_FILE_TOO_LARGE, HttpStatus.PAYLOAD_TOO_LARGE, "单文件不得超过 100MB");
        }
        if (!repository.knowledgeBaseExists(tenantId, kbId)) {
            throw new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.NOT_FOUND, "知识库不存在");
        }
        format(safeFileName(fileName));
    }

    static String safeFileName(String value) {
        if (value == null || value.isBlank()) return "upload.bin";
        String normalized = value.replace('\\', '/');
        String name = normalized.substring(normalized.lastIndexOf('/') + 1);
        if (name.isBlank() || name.length() > 255 || name.contains("\u0000")) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "文件名不合法");
        }
        return name;
    }

    static String format(String fileName) {
        int dot = fileName.lastIndexOf('.');
        String format = dot < 0 ? "" : fileName.substring(dot + 1).toUpperCase(Locale.ROOT);
        if (!FORMATS.contains(format)) {
            throw new ApiException(ErrorCodes.PARAM_UNSUPPORTED_FORMAT, HttpStatus.BAD_REQUEST, "不支持的文档格式");
        }
        return format;
    }

    private static String normalizeHash(String value) {
        String hash = value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
        if (!hash.matches("[a-f0-9]{64}")) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "fileHash 必须是 SHA-256 十六进制");
        }
        return hash;
    }

    private static void restrict(Path path) throws IOException {
        try {
            Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-------"));
        } catch (UnsupportedOperationException ignored) {
            // 非 POSIX 文件系统由部署侧 ACL 保护。
        }
    }

    private static void deleteQuietly(Path path) {
        if (path == null) return;
        try {
            Files.deleteIfExists(path);
        } catch (IOException ignored) {
            // 临时文件删除失败由部署侧临时目录清理策略兜底。
        }
    }

    private static String newId(String prefix) {
        return prefix + UUID.randomUUID().toString().replace("-", "");
    }

    public record InitUploadRequest(String fileName, long sizeBytes, String fileHash, Integer totalParts,
                                    String standardNo, Boolean autoParse) {
    }
}
