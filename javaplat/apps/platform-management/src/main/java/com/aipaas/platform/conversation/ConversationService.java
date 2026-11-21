package com.aipaas.platform.conversation;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Service
public class ConversationService {

    private static final String DEFAULT_TITLE = "新对话";
    private static final int MAX_TITLE = 200;
    private static final int MAX_CONTENT = 20000;

    private final ConversationRepository repository;
    private final ObjectMapper objectMapper;

    public ConversationService(ConversationRepository repository, ObjectMapper objectMapper) {
        this.repository = repository;
        this.objectMapper = objectMapper;
    }

    public Map<String, Object> list(String tenantId, String userId, String keyword) {
        List<Map<String, Object>> items = repository.list(tenantId, userId, keyword).stream()
                .map(this::view).toList();
        return Map.of("items", items, "total", items.size());
    }

    public Map<String, Object> create(String tenantId, String userId, String title) {
        String normalized = normalizeTitle(title);
        String id = newId("cnv_");
        repository.create(id, tenantId, userId, normalized, now());
        return view(repository.find(tenantId, userId, id).orElseThrow());
    }

    public Map<String, Object> rename(String tenantId, String userId, String id, String title) {
        String normalized = normalizeTitle(title);
        if (repository.rename(tenantId, userId, id, normalized, now()) != 1) {
            throw ApiException.notFound("对话不存在");
        }
        return view(repository.find(tenantId, userId, id).orElseThrow());
    }

    public Map<String, Object> delete(String tenantId, String userId, String id) {
        if (repository.softDelete(tenantId, userId, id, now()) != 1) {
            throw ApiException.notFound("对话不存在");
        }
        return Map.of("conversationId", id, "deleted", true);
    }

    public Map<String, Object> messages(String tenantId, String userId, String id) {
        requireConversation(tenantId, userId, id);
        List<Map<String, Object>> items = repository.messages(tenantId, userId, id).stream()
                .map(this::messageView).toList();
        return Map.of("items", items, "total", items.size());
    }

    @Transactional
    public Map<String, Object> clearMessages(String tenantId, String userId, String id) {
        requireConversation(tenantId, userId, id);
        int cleared = repository.clearMessages(tenantId, id);
        repository.touch(tenantId, userId, id, now());
        return Map.of("conversationId", id, "cleared", cleared);
    }

    public Map<String, Object> appendMessage(String tenantId, String userId, String id, String role, String content,
                                             Object citations) {
        requireConversation(tenantId, userId, id);
        String normalizedRole = normalizeRole(role);
        if (content == null || content.isBlank() || content.length() > MAX_CONTENT) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "消息内容不合法");
        }
        String citationsJson = null;
        if (citations != null) {
            try {
                citationsJson = objectMapper.writeValueAsString(citations);
            } catch (Exception ex) {
                throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "引用格式不合法");
            }
        }
        String messageId = newId("msg_");
        OffsetDateTime now = now();
        repository.addMessage(messageId, tenantId, id, userId, normalizedRole, content, citationsJson, now);
        repository.touch(tenantId, userId, id, now);
        return Map.of("id", messageId, "conversationId", id, "role", normalizedRole.toLowerCase(),
                "createdAt", now);
    }

    private void requireConversation(String tenantId, String userId, String id) {
        repository.find(tenantId, userId, id).orElseThrow(() -> ApiException.notFound("对话不存在"));
    }

    private Map<String, Object> view(ConversationRepository.ConversationRow row) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("id", row.id());
        data.put("title", row.title());
        data.put("createdAt", row.createdAt());
        data.put("updatedAt", row.updatedAt());
        return data;
    }

    private Map<String, Object> messageView(ConversationRepository.MessageRow row) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("id", row.id());
        data.put("role", row.role().toLowerCase());
        data.put("content", row.content());
        data.put("citations", parseCitations(row.citationsJson()));
        data.put("createdAt", row.createdAt());
        return data;
    }

    private JsonNode parseCitations(String json) {
        if (json == null || json.isBlank()) return objectMapper.createArrayNode();
        try {
            return objectMapper.readTree(json);
        } catch (Exception ex) {
            return objectMapper.createArrayNode();
        }
    }

    private static String normalizeTitle(String title) {
        String value = title == null ? "" : title.trim();
        if (value.isEmpty()) return DEFAULT_TITLE;
        return value.length() > MAX_TITLE ? value.substring(0, MAX_TITLE) : value;
    }

    private static String normalizeRole(String role) {
        String value = role == null ? "" : role.trim().toUpperCase();
        if ("USER".equals(value) || "ASSISTANT".equals(value) || "BOT".equals(value)) {
            return "BOT".equals(value) ? "ASSISTANT" : value;
        }
        throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "消息角色不合法");
    }

    private static OffsetDateTime now() {
        return OffsetDateTime.now(ZoneOffset.UTC);
    }

    private static String newId(String prefix) {
        return prefix + UUID.randomUUID().toString().replace("-", "");
    }
}
