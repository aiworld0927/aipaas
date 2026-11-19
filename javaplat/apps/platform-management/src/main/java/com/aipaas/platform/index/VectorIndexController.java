package com.aipaas.platform.index;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.rbac.RequirePermission;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/** #49 向量索引接口：构建、查询、重试、回退与知识库索引状态。 */
@RestController
@RequestMapping("/api/v1")
public class VectorIndexController {

    private final VectorIndexService indexes;

    public VectorIndexController(VectorIndexService indexes) {
        this.indexes = indexes;
    }

    @PostMapping("/index/builds")
    @ResponseStatus(HttpStatus.ACCEPTED)
    @RequirePermission("index:build")
    public ApiEnvelope<Map<String, Object>> create(@RequestBody VectorIndexService.IndexBuildRequest request,
                                                   @RequestHeader("Idempotency-Key") String idempotencyKey) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(indexes.create(principal.tenantId(), idempotencyKey, request),
                TraceContext.current());
    }

    @GetMapping("/index/builds/{buildId}")
    @RequirePermission("index:read")
    public ApiEnvelope<Map<String, Object>> get(@PathVariable String buildId) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(indexes.get(principal.tenantId(), buildId), TraceContext.current());
    }

    @PostMapping("/index/builds/{buildId}:retry")
    @ResponseStatus(HttpStatus.ACCEPTED)
    @RequirePermission("index:build")
    public ApiEnvelope<Map<String, Object>> retry(@PathVariable String buildId,
                                                  @RequestHeader("Idempotency-Key") String idempotencyKey) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(indexes.retry(principal.tenantId(), buildId), TraceContext.current());
    }

    /** P0 额外能力：主动回退到构建前的旧集合（失败构建不会切换别名，无需回退）。 */
    @PostMapping("/index/builds/{buildId}:rollback")
    @RequirePermission("index:build")
    public ApiEnvelope<Map<String, Object>> rollback(@PathVariable String buildId) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(indexes.rollback(principal.tenantId(), buildId), TraceContext.current());
    }

    @GetMapping("/knowledge-bases/{kbId}/index-status")
    @RequirePermission(value = "index:read", kbId = "kbId")
    public ApiEnvelope<Map<String, Object>> status(@PathVariable String kbId) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(indexes.status(principal.tenantId(), kbId), TraceContext.current());
    }
}
