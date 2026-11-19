package com.aipaas.platform.observability;

import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.rbac.RequirePermission;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** #175 按 TraceID 查询全链路 Span（Java + Python 聚合）。 */
@RestController
@RequestMapping("/api/v1/observability/traces")
public class TraceController {

    private final TraceQueryService service;

    public TraceController(TraceQueryService service) {
        this.service = service;
    }

    @GetMapping("/{traceId}")
    @RequirePermission("obs:read")
    public ResponseEntity<ApiEnvelope<?>> get(@PathVariable String traceId) {
        return ResponseEntity.ok(ApiEnvelope.ok(service.query(traceId), TraceContext.current()));
    }
}
