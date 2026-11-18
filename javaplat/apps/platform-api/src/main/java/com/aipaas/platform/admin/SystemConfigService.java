package com.aipaas.platform.admin;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** P0 系统配置（#193/#402）：默认能力、上传上限、检索参数；未配置时返回安全默认值。 */
@Service
public class SystemConfigService {

    public static final long P0_MAX_UPLOAD_BYTES = 100L * 1024L * 1024L;
    private static final List<String> SUPPORTED_FORMATS =
            List.of("PDF", "DOC", "DOCX", "XLS", "XLSX", "PPT", "PPTX", "MD", "TXT");
    private static final Set<String> CAPABILITIES = Set.of("RAG", "SKILL");
    private static final Set<String> PATCH_KEYS =
            Set.of("defaultCapability", "maxUploadSizeBytes", "maxQueryLength", "streamingEnabled", "retrievalTopK");

    private final SystemConfigRepository repository;
    private final ObjectMapper objectMapper;

    public SystemConfigService(SystemConfigRepository repository, ObjectMapper objectMapper) {
        this.repository = repository;
        this.objectMapper = objectMapper;
    }

    public Map<String, Object> get(String tenantId) {
        Map<String, Object> config = defaults();
        repository.find(tenantId).ifPresent(row -> config.putAll(readJson(row.configJson())));
        return config;
    }

    @Transactional
    public Map<String, Object> update(String tenantId, String actorId, String defaultCapability,
                                      Long maxUploadSizeBytes, Integer maxQueryLength, Boolean streamingEnabled,
                                      Integer retrievalTopK) {
        if (defaultCapability == null && maxUploadSizeBytes == null && maxQueryLength == null
                && streamingEnabled == null && retrievalTopK == null) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "至少需要提供一个配置项");
        }
        Map<String, Object> config = new LinkedHashMap<>(get(tenantId));
        if (defaultCapability != null) {
            if (!CAPABILITIES.contains(defaultCapability)) {
                throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "默认能力取值不合法");
            }
            config.put("defaultCapability", defaultCapability);
        }
        if (maxUploadSizeBytes != null) {
            if (maxUploadSizeBytes < 1 || maxUploadSizeBytes > P0_MAX_UPLOAD_BYTES) {
                throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST,
                        "上传上限取值不合法，P0 最大为 " + P0_MAX_UPLOAD_BYTES + " 字节");
            }
            config.put("maxUploadSizeBytes", maxUploadSizeBytes);
        }
        if (maxQueryLength != null) {
            if (maxQueryLength < 1 || maxQueryLength > 8000) {
                throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "查询长度上限取值不合法");
            }
            config.put("maxQueryLength", maxQueryLength);
        }
        if (streamingEnabled != null) {
            config.put("streamingEnabled", streamingEnabled);
        }
        if (retrievalTopK != null) {
            if (retrievalTopK < 1 || retrievalTopK > 50) {
                throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "retrievalTopK 取值范围为 1..50");
            }
            config.put("retrievalTopK", retrievalTopK);
        }
        config.put("supportedFormats", SUPPORTED_FORMATS);
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        config.put("updatedAt", now.toString());
        try {
            repository.save(tenantId, objectMapper.writeValueAsString(config), actorId, now);
        } catch (com.fasterxml.jackson.core.JsonProcessingException ex) {
            throw new IllegalStateException("无法序列化系统配置", ex);
        }
        return config;
    }

    private Map<String, Object> defaults() {
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("defaultCapability", "RAG");
        config.put("supportedFormats", SUPPORTED_FORMATS);
        config.put("maxUploadSizeBytes", P0_MAX_UPLOAD_BYTES);
        config.put("maxQueryLength", 2000);
        config.put("streamingEnabled", true);
        config.put("retrievalTopK", 5);
        return config;
    }

    private Map<String, Object> readJson(String json) {
        try {
            Map<String, Object> stored = objectMapper.readValue(json, new TypeReference<LinkedHashMap<String, Object>>() {
            });
            stored.keySet().retainAll(java.util.stream.Stream.concat(PATCH_KEYS.stream(),
                    java.util.stream.Stream.of("updatedAt")).toList());
            return stored;
        } catch (com.fasterxml.jackson.core.JsonProcessingException ex) {
            throw new IllegalStateException("系统配置格式损坏", ex);
        }
    }
}
