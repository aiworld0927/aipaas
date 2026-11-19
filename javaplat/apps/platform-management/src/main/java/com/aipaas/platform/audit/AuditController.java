package com.aipaas.platform.audit;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.rbac.RequirePermission;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;

@RestController
@RequestMapping("/api/v1/audit-logs")
public class AuditController {

    private final AuditService auditService;

    public AuditController(AuditService auditService) {
        this.auditService = auditService;
    }

    @GetMapping
    @RequirePermission("audit:read")
    public ResponseEntity<?> search(@RequestParam(defaultValue = "1") int page,
                                    @RequestParam(defaultValue = "20") int pageSize,
                                    @RequestParam(required = false) String action,
                                    @RequestParam(required = false) String actorId,
                                    @RequestParam(required = false) String objectType,
                                    @RequestParam(required = false) String objectId,
                                    @RequestParam(required = false) String result,
                                    @RequestParam(required = false) OffsetDateTime from,
                                    @RequestParam(required = false) OffsetDateTime to,
                                    @RequestParam(defaultValue = "json") String format,
                                    @RequestParam(defaultValue = "false") boolean archive,
                                    @RequestParam(defaultValue = "prod") String env) {
        if (archive) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "长期存档查询为 P2 预留能力");
        }
        if (page < 1 || pageSize < 1 || pageSize > 100 || !format.matches("json|csv")
                || (result != null && !result.matches("SUCCESS|FAILURE|DENIED"))) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "审计查询参数不合法");
        }
        SecurityPrincipal principal = SecurityPrincipal.current();
        AuditQuery query = new AuditQuery(principal.tenantId(), env, action, actorId, objectType, objectId, result, from, to);
        if ("csv".equals(format)) {
            String csv = auditService.exportCsv(query);
            return ResponseEntity.ok()
                    .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=audit-logs.csv")
                    .contentType(new MediaType("text", "csv", StandardCharsets.UTF_8)).body(csv);
        }
        return ResponseEntity.ok(ApiEnvelope.ok(auditService.search(query, page, pageSize), TraceContext.current()));
    }
}
