package com.aipaas.platform.export;

import com.aipaas.platform.audit.AuditService;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.document.DocumentRepository;
import com.aipaas.platform.rbac.RbacService;
import com.aipaas.platform.rbac.RequirePermission;
import com.aipaas.platform.common.web.TraceContext;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/** #405 标准简介入口：文档归属/KB 权限校验后代理解析服务，并审计生成动作。 */
@RestController
@RequestMapping("/api/v1/documents/{documentId}/summaries")
public class SummaryController {

    private final SummaryProxyService summaries;
    private final DocumentRepository documents;
    private final RbacService rbac;
    private final AuditService audit;

    public SummaryController(SummaryProxyService summaries, DocumentRepository documents, RbacService rbac,
                             AuditService audit) {
        this.summaries = summaries;
        this.documents = documents;
        this.rbac = rbac;
        this.audit = audit;
    }

    @PostMapping
    @RequirePermission("doc:read")
    public ApiEnvelope<Map<String, Object>> generate(@PathVariable String documentId,
                                                     @RequestBody(required = false) GenerateRequest request) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        DocumentRepository.DocumentRow document = requireDocument(documentId, principal);
        requireAccess(principal, document, "summary.generate");
        Map<String, Object> data = summaries.generate(documentId, principal.tenantId(),
                request != null && request.regenerate());
        audit.record(principal, "summary.generate", "document", documentId, "SUCCESS",
                Map.of("kbId", document.kbId(), "documentVersion",
                        String.valueOf(data.getOrDefault("documentVersion", "")),
                        "extractor", String.valueOf(data.getOrDefault("promptTemplateVersion", ""))));
        return ApiEnvelope.ok(data, TraceContext.current());
    }

    @GetMapping("/latest")
    @RequirePermission("doc:read")
    public ApiEnvelope<Map<String, Object>> latest(@PathVariable String documentId) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        DocumentRepository.DocumentRow document = requireDocument(documentId, principal);
        requireAccess(principal, document, "summary.read");
        return ApiEnvelope.ok(summaries.latest(documentId, principal.tenantId()), TraceContext.current());
    }

    private DocumentRepository.DocumentRow requireDocument(String documentId, SecurityPrincipal principal) {
        return documents.findActive(principal.tenantId(), documentId)
                .orElseThrow(() -> new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.NOT_FOUND, "文档不存在"));
    }

    private void requireAccess(SecurityPrincipal principal, DocumentRepository.DocumentRow document, String action) {
        if (rbac.allowed(principal, "doc:read", document.kbId())) {
            return;
        }
        audit.record(principal, action, "document", document.id(), "DENIED", Map.of("kbId", document.kbId()));
        throw new ApiException(ErrorCodes.PERM_DOC_FORBIDDEN, HttpStatus.FORBIDDEN, "无权访问该文档");
    }

    public record GenerateRequest(boolean regenerate) {
    }
}
