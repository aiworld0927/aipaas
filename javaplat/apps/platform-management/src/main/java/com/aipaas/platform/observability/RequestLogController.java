package com.aipaas.platform.observability;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.rbac.RequirePermission;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.OffsetDateTime;

/** #173 请求日志检索：按 TraceID/用户/接口/状态码/时间范围查询，租户隔离。 */
@RestController
@RequestMapping("/api/v1/observability/request-logs")
public class RequestLogController {

    private final RequestLogService service;

    public RequestLogController(RequestLogService service) {
        this.service = service;
    }

    @GetMapping
    @RequirePermission("obs:read")
    public ResponseEntity<ApiEnvelope<?>> search(@RequestParam(defaultValue = "1") int page,
                                                 @RequestParam(defaultValue = "20") int pageSize,
                                                 @RequestParam(required = false) String traceId,
                                                 @RequestParam(required = false) String userId,
                                                 @RequestParam(required = false) String path,
                                                 @RequestParam(required = false) Integer status,
                                                 @RequestParam(required = false) OffsetDateTime from,
                                                 @RequestParam(required = false) OffsetDateTime to) {
        if (page < 1 || pageSize < 1 || pageSize > 100
                || (status != null && (status < 100 || status > 599))) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "请求日志查询参数不合法");
        }
        SecurityPrincipal principal = SecurityPrincipal.current();
        RequestLogQuery query = new RequestLogQuery(principal.tenantId(), blankToNull(traceId),
                blankToNull(userId), blankToNull(path), status, from, to);
        return ResponseEntity.ok(ApiEnvelope.ok(service.search(query, page, pageSize), TraceContext.current()));
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
