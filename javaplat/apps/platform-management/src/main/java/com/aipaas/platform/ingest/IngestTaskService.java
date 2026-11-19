package com.aipaas.platform.ingest;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** #10 接入任务状态机：列表、详情、一键重试与状态事件。 */
@Service
public class IngestTaskService {

    private static final Set<String> TERMINAL = Set.of("SUCCEEDED", "FAILED", "CANCELED", "CANCELLED");

    private final IngestTaskRepository repository;
    private final IngestEventPublisher events;
    private final IngestTaskDispatcher dispatcher;

    public IngestTaskService(IngestTaskRepository repository, IngestEventPublisher events,
                             IngestTaskDispatcher dispatcher) {
        this.repository = repository;
        this.events = events;
        this.dispatcher = dispatcher;
    }

    public Map<String, Object> list(String tenantId, String kbId, String documentId, String status,
                                    int page, int pageSize) {
        if (page < 1 || pageSize < 1 || pageSize > 100) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "分页参数不合法");
        }
        int offset = (page - 1) * pageSize;
        List<Map<String, Object>> items = repository.list(tenantId, kbId, documentId, status, pageSize, offset)
                .stream().map(this::view).toList();
        int total = repository.count(tenantId, kbId, documentId, status);
        return Map.of("items", items, "total", total, "page", page, "pageSize", pageSize);
    }

    public Map<String, Object> get(String tenantId, String taskId) {
        return view(require(tenantId, taskId));
    }

    /** 失败任务一键重试：复用同一任务行，不新增任务与文档。 */
    public Map<String, Object> retry(String tenantId, String taskId) {
        IngestTaskRepository.TaskRow task = require(tenantId, taskId);
        if (!"FAILED".equals(task.status())) {
            throw new ApiException(ErrorCodes.BIZ_TASK_NOT_RETRYABLE, HttpStatus.CONFLICT,
                    "仅失败任务可重试，当前状态：" + task.status());
        }
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        if (!repository.requeueForRetry(taskId, now)) {
            throw new ApiException(ErrorCodes.BIZ_CONFLICT, HttpStatus.CONFLICT, "任务状态已变化，请刷新后重试");
        }
        repository.markDocument(tenantId, task.documentId(), "QUEUED", null, null, now);
        IngestTaskRepository.TaskRow queued = repository.find(tenantId, taskId).orElse(task);
        events.publish(taskId, "status", event(queued));
        dispatcher.dispatch(taskId);
        return view(queued);
    }

    public Map<String, Object> snapshot(String tenantId, String taskId) {
        return event(require(tenantId, taskId));
    }

    public boolean terminal(String status) {
        return TERMINAL.contains(status);
    }

    private IngestTaskRepository.TaskRow require(String tenantId, String taskId) {
        return repository.find(tenantId, taskId).orElseThrow(() ->
                new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.NOT_FOUND, "接入任务不存在"));
    }

    private Map<String, Object> view(IngestTaskRepository.TaskRow task) {
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("id", task.id());
        view.put("tenantId", task.tenantId());
        view.put("documentId", task.documentId());
        view.put("kbId", task.kbId());
        view.put("status", task.status());
        view.put("stage", task.stage());
        view.put("progress", task.progress());
        view.put("retryCount", task.retryCount());
        view.put("maxRetries", task.maxRetries());
        if (task.errorCode() != null) {
            Map<String, Object> error = new LinkedHashMap<>();
            error.put("code", task.errorCode());
            error.put("message", task.errorMessage());
            error.put("retryable", Boolean.TRUE.equals(task.retryable()));
            error.put("stage", task.stage());
            view.put("error", error);
        }
        view.put("queuedAt", task.queuedAt());
        view.put("startedAt", task.startedAt());
        view.put("finishedAt", task.finishedAt());
        view.put("createdAt", task.createdAt());
        view.put("updatedAt", task.updatedAt());
        return view;
    }

    Map<String, Object> event(IngestTaskRepository.TaskRow task) {
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("taskId", task.id());
        event.put("status", task.status());
        event.put("stage", task.stage());
        event.put("progress", task.progress());
        event.put("at", OffsetDateTime.now(ZoneOffset.UTC));
        if (task.errorCode() != null) {
            event.put("code", task.errorCode());
            event.put("message", task.errorMessage());
        }
        return event;
    }
}
