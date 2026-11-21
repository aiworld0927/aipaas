package com.aipaas.platform.conversation;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.web.TraceContext;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/** 对话管理：新建、搜索、重命名、删除与消息持久化，全部按当前用户隔离。 */
@RestController
@RequestMapping("/api/v1/conversations")
public class ConversationController {

    private final ConversationService conversations;

    public ConversationController(ConversationService conversations) {
        this.conversations = conversations;
    }

    @GetMapping
    public ApiEnvelope<Map<String, Object>> list(@RequestParam(required = false) String keyword) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(conversations.list(principal.tenantId(), principal.userId(), keyword),
                TraceContext.current());
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ApiEnvelope<Map<String, Object>> create(@RequestBody(required = false) CreateRequest request) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(conversations.create(principal.tenantId(), principal.userId(),
                request == null ? null : request.title()), TraceContext.current());
    }

    @PatchMapping("/{conversationId}")
    public ApiEnvelope<Map<String, Object>> rename(@PathVariable String conversationId,
                                                   @RequestBody RenameRequest request) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(conversations.rename(principal.tenantId(), principal.userId(), conversationId,
                request.title()), TraceContext.current());
    }

    @DeleteMapping("/{conversationId}")
    public ApiEnvelope<Map<String, Object>> delete(@PathVariable String conversationId) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(conversations.delete(principal.tenantId(), principal.userId(), conversationId),
                TraceContext.current());
    }

    @DeleteMapping("/{conversationId}/messages")
    public ApiEnvelope<Map<String, Object>> clearMessages(@PathVariable String conversationId) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(conversations.clearMessages(principal.tenantId(), principal.userId(), conversationId),
                TraceContext.current());
    }

    @GetMapping("/{conversationId}/messages")
    public ApiEnvelope<Map<String, Object>> messages(@PathVariable String conversationId) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(conversations.messages(principal.tenantId(), principal.userId(), conversationId),
                TraceContext.current());
    }

    @PostMapping("/{conversationId}/messages")
    @ResponseStatus(HttpStatus.CREATED)
    public ApiEnvelope<Map<String, Object>> appendMessage(@PathVariable String conversationId,
                                                          @RequestBody MessageRequest request) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(conversations.appendMessage(principal.tenantId(), principal.userId(), conversationId,
                request.role(), request.content(), request.citations()), TraceContext.current());
    }

    public record CreateRequest(@Size(max = 200) String title) {
    }

    public record RenameRequest(@Size(max = 200) String title) {
    }

    public record MessageRequest(String role, String content, Object citations) {
    }
}
