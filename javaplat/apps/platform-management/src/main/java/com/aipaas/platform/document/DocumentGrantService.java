package com.aipaas.platform.document;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 文档级权限：知识库级授权继续由 @RequirePermission 校验，
 * 文档级 ACL 仅在该文档配置了授权条目时收紧访问范围（owner / 平台管理员始终放行）。
 */
@Service
public class DocumentGrantService {

    private static final Set<String> SUBJECT_TYPES = Set.of("USER", "ROLE");
    private static final Set<String> PERMISSIONS = Set.of("READ", "WRITE");

    private final DocumentGrantRepository grants;
    private final DocumentRepository documents;

    public DocumentGrantService(DocumentGrantRepository grants, DocumentRepository documents) {
        this.grants = grants;
        this.documents = documents;
    }

    public Map<String, Object> list(String tenantId, String documentId) {
        DocumentRepository.DocumentRow document = requireDocument(tenantId, documentId);
        List<Map<String, Object>> items = grants.list(tenantId, documentId).stream().map(this::view).toList();
        return Map.of("documentId", document.id(), "items", items, "total", items.size());
    }

    @Transactional
    public Map<String, Object> replace(String tenantId, String documentId, SecurityPrincipal principal,
                                       List<DocumentGrantRepository.GrantInput> inputs) {
        requireDocument(tenantId, documentId);
        List<DocumentGrantRepository.GrantInput> normalized = normalize(tenantId, inputs);
        grants.replace(tenantId, documentId, principal.userId(), normalized);
        List<Map<String, Object>> items = grants.list(tenantId, documentId).stream().map(this::view).toList();
        return Map.of("documentId", documentId, "items", items, "total", items.size());
    }

    /** 无文档级 ACL 时返回 true，沿用知识库级授权；配置 ACL 后仅授权主体可访问。 */
    public boolean canAccess(DocumentRepository.DocumentRow document, SecurityPrincipal principal, String need) {
        if (principal.permissions().contains("*") || principal.roles().contains("PLATFORM_ADMIN")) return true;
        if (document.uploaderId() != null && document.uploaderId().equals(principal.userId())) return true;
        List<DocumentGrantRepository.GrantRow> rows = grants.list(document.tenantId(), document.id());
        if (rows.isEmpty()) return true;
        for (DocumentGrantRepository.GrantRow row : rows) {
            boolean subjectMatch = ("USER".equals(row.subjectType()) && row.subjectId().equals(principal.userId()))
                    || ("ROLE".equals(row.subjectType()) && principal.roles().contains(row.subjectId()));
            if (subjectMatch && covers(need, row.permission())) return true;
        }
        return false;
    }

    public void requireAccess(DocumentRepository.DocumentRow document, SecurityPrincipal principal, String need) {
        if (!canAccess(document, principal, need)) {
            throw new ApiException(ErrorCodes.PERM_FORBIDDEN, HttpStatus.FORBIDDEN, "无权访问该文档");
        }
    }

    private static boolean covers(String need, String granted) {
        return "READ".equals(need) || "WRITE".equals(granted);
    }

    private List<DocumentGrantRepository.GrantInput> normalize(String tenantId,
                                                               List<DocumentGrantRepository.GrantInput> inputs) {
        if (inputs == null) return List.of();
        List<DocumentGrantRepository.GrantInput> result = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        for (DocumentGrantRepository.GrantInput input : inputs) {
            String subjectType = input.subjectType() == null ? "" : input.subjectType().trim().toUpperCase();
            String subjectId = input.subjectId() == null ? "" : input.subjectId().trim();
            String permission = input.permission() == null ? "" : input.permission().trim().toUpperCase();
            if (!SUBJECT_TYPES.contains(subjectType) || !PERMISSIONS.contains(permission) || subjectId.isEmpty()) {
                throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "授权条目格式不合法");
            }
            if (!grants.subjectExists(tenantId, subjectType, subjectId)) {
                throw new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.BAD_REQUEST,
                        "授权对象不存在: " + subjectId);
            }
            String key = subjectType + "|" + subjectId + "|" + permission;
            if (seen.add(key)) {
                result.add(new DocumentGrantRepository.GrantInput(subjectType, subjectId, permission));
            }
        }
        return result;
    }

    private DocumentRepository.DocumentRow requireDocument(String tenantId, String documentId) {
        return documents.findActive(tenantId, documentId).orElseThrow(() ->
                new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.NOT_FOUND, "文档不存在"));
    }

    private Map<String, Object> view(DocumentGrantRepository.GrantRow row) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("subjectType", row.subjectType());
        data.put("subjectId", row.subjectId());
        data.put("permission", row.permission());
        data.put("grantedBy", row.grantedBy());
        data.put("createdAt", row.createdAt());
        return data;
    }
}
