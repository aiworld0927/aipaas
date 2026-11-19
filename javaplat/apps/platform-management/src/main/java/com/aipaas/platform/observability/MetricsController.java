package com.aipaas.platform.observability;

import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.rbac.RequirePermission;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** #177 QPS/延迟/成功率/资源占用汇总，供前端与管理看板查询。 */
@RestController
@RequestMapping("/api/v1/observability/metrics")
public class MetricsController {

    private final ObservabilityMetrics metrics;

    public MetricsController(ObservabilityMetrics metrics) {
        this.metrics = metrics;
    }

    @GetMapping("/summary")
    @RequirePermission("obs:read")
    public ResponseEntity<ApiEnvelope<?>> summary(
            @RequestParam(defaultValue = "15m") String window) {
        return ResponseEntity.ok(ApiEnvelope.ok(metrics.summary(window), TraceContext.current()));
    }
}
