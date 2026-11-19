package com.aipaas.platform.ingest;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.rbac.RequirePermission;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.Map;

/** #10 接入任务接口：列表、详情、一键重试与 SSE 状态推送。 */
@RestController
@RequestMapping("/api/v1")
public class IngestTaskController {

    private final IngestTaskService tasks;
    private final IngestEventPublisher events;

    public IngestTaskController(IngestTaskService tasks, IngestEventPublisher events) {
        this.tasks = tasks;
        this.events = events;
    }

    @GetMapping("/ingest/tasks")
    @RequirePermission("ingest:read")
    public ApiEnvelope<Map<String, Object>> list(@RequestParam(defaultValue = "1") int page,
                                                 @RequestParam(defaultValue = "20") int pageSize,
                                                 @RequestParam(required = false) String kbId,
                                                 @RequestParam(required = false) String documentId,
                                                 @RequestParam(required = false) String status) {
        return ApiEnvelope.ok(tasks.list(SecurityPrincipal.current().tenantId(), kbId, documentId, status,
                page, pageSize), TraceContext.current());
    }

    @GetMapping("/ingest/tasks/{taskId}")
    @RequirePermission("ingest:read")
    public ApiEnvelope<Map<String, Object>> get(@PathVariable String taskId) {
        return ApiEnvelope.ok(tasks.get(SecurityPrincipal.current().tenantId(), taskId), TraceContext.current());
    }

    /** 一键重试：仅 FAILED 可重试，复用同一任务行，返回 202。 */
    @PostMapping("/ingest/tasks/{taskId}:retry")
    @ResponseStatus(HttpStatus.ACCEPTED)
    @RequirePermission("ingest:retry")
    public ApiEnvelope<Map<String, Object>> retry(@PathVariable String taskId,
                                                  @RequestHeader("Idempotency-Key") String idempotencyKey) {
        return ApiEnvelope.ok(tasks.retry(SecurityPrincipal.current().tenantId(), taskId), TraceContext.current());
    }

    @GetMapping(value = "/ingest/tasks/{taskId}/events", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    @RequirePermission("ingest:read")
    public SseEmitter events(@PathVariable String taskId) {
        String tenantId = SecurityPrincipal.current().tenantId();
        Map<String, Object> snapshot = tasks.snapshot(tenantId, taskId);
        SseEmitter emitter = events.subscribe(taskId, snapshot);
        Object status = snapshot.get("status");
        if (status != null && tasks.terminal(String.valueOf(status))) {
            events.complete(taskId, "done", Map.of("taskId", taskId, "status", String.valueOf(status)));
        }
        return emitter;
    }
}
