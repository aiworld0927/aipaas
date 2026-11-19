package com.aipaas.platform.knowledgebase;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.document.DocumentRepository;
import com.aipaas.platform.document.IndexCleanupService;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Service
public class KnowledgeBaseService {

    private static final int DEFAULT_CHUNK_SIZE = 512;
    private static final int DEFAULT_CHUNK_OVERLAP = 64;
    private static final int DEFAULT_TOP_K = 5;

    private final KnowledgeBaseRepository repository;
    private final DocumentRepository documents;
    private final IndexCleanupService cleanup;
    private final ObjectMapper objectMapper;

    public KnowledgeBaseService(KnowledgeBaseRepository repository, DocumentRepository documents,
                                IndexCleanupService cleanup, ObjectMapper objectMapper) {
        this.repository = repository;
        this.documents = documents;
        this.cleanup = cleanup;
        this.objectMapper = objectMapper;
    }

    public Map<String, Object> list(String tenantId, int page, int pageSize, String keyword, String domain) {
        if (page < 1 || pageSize < 1 || pageSize > 100) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "分页参数不合法");
        }
        long offset = (long) (page - 1) * pageSize;
        List<Map<String, Object>> items = repository.page(tenantId, keyword, domain, pageSize, offset).stream()
                .map(this::toView).toList();
        return Map.of("items", items, "total", repository.count(tenantId, keyword, domain),
                "page", page, "pageSize", pageSize);
    }

    public Map<String, Object> get(String tenantId, String kbId) {
        return toView(require(tenantId, kbId));
    }

    @Transactional
    public Map<String, Object> create(String tenantId, String actor, String name, String description, String domain,
                                      String standardSystem, Map<String, Object> config) {
        String trimmed = name == null ? "" : name.trim();
        if (trimmed.isEmpty() || trimmed.length() > 128) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "知识库名称不能为空且不超过 128 字符");
        }
        if (repository.nameExists(tenantId, trimmed)) {
            throw new ApiException(ErrorCodes.BIZ_CONFLICT, HttpStatus.CONFLICT, "同名知识库已存在");
        }
        String id = "kb_" + UUID.randomUUID().toString().replace("-", "");
        repository.insert(id, tenantId, trimmed, normalize(description, 1024), normalize(domain, 64),
                normalize(standardSystem, 128), writeConfig(config), actor, OffsetDateTime.now());
        return toView(require(tenantId, id));
    }

    @Transactional
    public Map<String, Object> update(String tenantId, String kbId, String name, String description, String domain,
                                      String standardSystem, Map<String, Object> config) {
        KnowledgeBaseRepository.Row current = require(tenantId, kbId);
        String nextName = name == null || name.isBlank() ? current.name() : name.trim();
        if (nextName.length() > 128) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "知识库名称过长");
        }
        if (!nextName.equals(current.name()) && repository.nameExists(tenantId, nextName)) {
            throw new ApiException(ErrorCodes.BIZ_CONFLICT, HttpStatus.CONFLICT, "同名知识库已存在");
        }
        String nextConfig = config == null ? current.configJson() : writeConfig(config);
        repository.update(tenantId, kbId, nextName, valueOr(description, current.description(), 1024),
                valueOr(domain, current.domain(), 64), valueOr(standardSystem, current.standardSystem(), 128),
                nextConfig, OffsetDateTime.now());
        return toView(require(tenantId, kbId));
    }

    @Transactional
    public Map<String, Object> delete(String tenantId, String kbId) {
        require(tenantId, kbId);
        List<DocumentRepository.DocumentRow> active = documents.activeDocuments(tenantId, kbId);
        OffsetDateTime now = OffsetDateTime.now();
        documents.softDeleteByKb(tenantId, kbId, now);
        active.forEach(document -> cleanup.enqueue(tenantId, document.id(), kbId, document.objectKey()));
        repository.markDeleted(tenantId, kbId, now);
        return Map.of("kbId", kbId, "deleted", true, "documents", active.size());
    }

    public KnowledgeBaseRepository.Row require(String tenantId, String kbId) {
        return repository.find(tenantId, kbId).orElseThrow(() ->
                new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.NOT_FOUND, "知识库不存在"));
    }

    private Map<String, Object> toView(KnowledgeBaseRepository.Row row) {
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("id", row.id());
        view.put("tenantId", row.tenantId());
        view.put("name", row.name());
        view.put("description", row.description());
        view.put("domain", row.domain());
        view.put("standardSystem", row.standardSystem());
        view.put("status", row.status());
        view.put("indexStatus", effectiveIndexStatus(row));
        view.put("documentCount", row.documentCount());
        view.put("chunkCount", row.chunkCount());
        view.put("config", readConfig(row.configJson()));
        view.put("createdBy", row.createdBy());
        view.put("createdAt", row.createdAt() == null ? null : row.createdAt().toString());
        view.put("updatedAt", row.updatedAt() == null ? null : row.updatedAt().toString());
        return view;
    }

    static String effectiveIndexStatus(KnowledgeBaseRepository.Row row) {
        if (row.failedIndex() > 0) return "FAILED";
        if (row.activeDocs() > 0 || row.pendingIndex() > 0) return "BUILDING";
        return row.documentCount() == 0 ? "EMPTY" : "READY";
    }

    private String writeConfig(Map<String, Object> config) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("chunkSize", intConfig(config, "chunkSize", DEFAULT_CHUNK_SIZE, 64, 2048));
        value.put("chunkOverlap", intConfig(config, "chunkOverlap", DEFAULT_CHUNK_OVERLAP, 0, 512));
        value.put("retrievalTopK", intConfig(config, "retrievalTopK", DEFAULT_TOP_K, 1, 20));
        if ((int) value.get("chunkOverlap") >= (int) value.get("chunkSize")) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "chunkOverlap 必须小于 chunkSize");
        }
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException ex) {
            throw new ApiException(ErrorCodes.SYS_INTERNAL, HttpStatus.INTERNAL_SERVER_ERROR, "知识库配置序列化失败");
        }
    }

    private Map<String, Object> readConfig(String json) {
        if (json == null || json.isBlank()) {
            return Map.of("chunkSize", DEFAULT_CHUNK_SIZE, "chunkOverlap", DEFAULT_CHUNK_OVERLAP,
                    "retrievalTopK", DEFAULT_TOP_K);
        }
        try {
            return objectMapper.readValue(json, new com.fasterxml.jackson.core.type.TypeReference<>() {
            });
        } catch (JsonProcessingException ex) {
            return Map.of();
        }
    }

    private static int intConfig(Map<String, Object> config, String key, int fallback, int min, int max) {
        if (config == null || !(config.get(key) instanceof Number number)) {
            return fallback;
        }
        int value = number.intValue();
        if (value < min || value > max) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, key + " 超出允许范围");
        }
        return value;
    }

    private static String valueOr(String candidate, String fallback, int max) {
        if (candidate == null) return fallback;
        return normalize(candidate, max);
    }

    private static String normalize(String value, int max) {
        if (value == null) return null;
        String trimmed = value.trim();
        if (trimmed.length() > max) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "字段长度超出限制");
        }
        return trimmed.isEmpty() ? null : trimmed;
    }
}
