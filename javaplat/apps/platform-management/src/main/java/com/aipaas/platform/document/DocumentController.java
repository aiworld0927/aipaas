package com.aipaas.platform.document;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.rbac.RequirePermission;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.util.Map;

@RestController
@RequestMapping("/api/v1")
public class DocumentController {

    private final DocumentService documents;

    public DocumentController(DocumentService documents) {
        this.documents = documents;
    }

    @PostMapping("/knowledge-bases/{kbId}/documents")
    @ResponseStatus(HttpStatus.ACCEPTED)
    @RequirePermission(value = "doc:upload", kbId = "kbId")
    public ApiEnvelope<Map<String, Object>> upload(@PathVariable String kbId,
                                                   @RequestPart("file") MultipartFile file,
                                                   @RequestParam(required = false) String standardNo,
                                                   @RequestParam(defaultValue = "true") boolean autoParse,
                                                   @RequestHeader("Idempotency-Key") String idempotencyKey) {
        return ApiEnvelope.ok(documents.upload(kbId, file, standardNo, autoParse, SecurityPrincipal.current()),
                TraceContext.current());
    }

    /* ---------------------------- 分片上传（P0 #1 / P1 #253 复用） ---------------------------- */

    @PostMapping("/knowledge-bases/{kbId}/documents/uploads:init")
    @RequirePermission(value = "doc:upload", kbId = "kbId")
    public ApiEnvelope<Map<String, Object>> initUpload(@PathVariable String kbId,
                                                       @RequestBody DocumentService.InitUploadRequest request,
                                                       @RequestHeader("Idempotency-Key") String idempotencyKey) {
        return ApiEnvelope.ok(documents.initUpload(kbId, request, SecurityPrincipal.current()), TraceContext.current());
    }

    @GetMapping("/knowledge-bases/{kbId}/documents/uploads/{uploadId}")
    @RequirePermission(value = "doc:upload", kbId = "kbId")
    public ApiEnvelope<Map<String, Object>> uploadStatus(@PathVariable String kbId, @PathVariable String uploadId) {
        return ApiEnvelope.ok(documents.uploadStatus(kbId, uploadId, SecurityPrincipal.current()),
                TraceContext.current());
    }

    @PutMapping("/knowledge-bases/{kbId}/documents/uploads/{uploadId}/parts/{partNo}")
    @RequirePermission(value = "doc:upload", kbId = "kbId")
    public ApiEnvelope<Map<String, Object>> uploadPart(@PathVariable String kbId, @PathVariable String uploadId,
                                                       @PathVariable int partNo,
                                                       @RequestParam(required = false) Long offset,
                                                       HttpServletRequest request) {
        try (InputStream body = request.getInputStream()) {
            long declared = request.getContentLengthLong() >= 0 ? request.getContentLengthLong() : -1;
            return ApiEnvelope.ok(documents.putPart(kbId, uploadId, partNo, offset,
                    declared >= 0 ? declared : null, body, SecurityPrincipal.current()), TraceContext.current());
        } catch (IOException ex) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "分片读取失败");
        }
    }

    @PostMapping("/knowledge-bases/{kbId}/documents/uploads/{uploadId}:complete")
    @ResponseStatus(HttpStatus.ACCEPTED)
    @RequirePermission(value = "doc:upload", kbId = "kbId")
    public ApiEnvelope<Map<String, Object>> completeUpload(@PathVariable String kbId, @PathVariable String uploadId,
                                                           @RequestHeader("Idempotency-Key") String idempotencyKey) {
        return ApiEnvelope.ok(documents.completeUpload(kbId, uploadId, SecurityPrincipal.current()),
                TraceContext.current());
    }

    /* ---------------------------- 文档管理（P0 #134） ---------------------------- */

    @GetMapping("/documents")
    @RequirePermission("doc:read")
    public ApiEnvelope<?> list(@RequestParam(defaultValue = "1") int page,
                               @RequestParam(defaultValue = "20") int pageSize,
                               @RequestParam(required = false) String kbId,
                               @RequestParam(required = false) String status,
                               @RequestParam(required = false) String keyword) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(documents.list(principal.tenantId(), kbId, status, keyword, page, pageSize),
                TraceContext.current());
    }

    @GetMapping("/documents/{documentId}")
    @RequirePermission("doc:read")
    public ApiEnvelope<?> get(@PathVariable String documentId) {
        return ApiEnvelope.ok(documents.get(SecurityPrincipal.current().tenantId(), documentId),
                TraceContext.current());
    }

    @DeleteMapping("/documents/{documentId}")
    @RequirePermission("doc:delete")
    public ApiEnvelope<?> delete(@PathVariable String documentId) {
        return ApiEnvelope.ok(documents.delete(SecurityPrincipal.current().tenantId(), documentId),
                TraceContext.current());
    }
}
