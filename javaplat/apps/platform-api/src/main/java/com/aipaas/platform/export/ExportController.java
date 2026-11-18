package com.aipaas.platform.export;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.rbac.RequirePermission;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/** #406 导出资源：创建 → 查询 → 下载；兼容 /kbs/{kbId}/exports 与 /exports。 */
@RestController
@RequestMapping("/api/v1")
public class ExportController {

    private final ExportService service;

    public ExportController(ExportService service) {
        this.service = service;
    }

    @PostMapping("/kbs/{kbId}/exports")
    @RequirePermission("export:create")
    public ResponseEntity<ApiEnvelope<ExportService.ExportView>> createForKb(
            @PathVariable String kbId,
            @RequestBody ExportService.CreateExportRequest request,
            @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey) {
        return ResponseEntity.status(HttpStatus.ACCEPTED)
                .body(ApiEnvelope.ok(service.create(kbId, request, SecurityPrincipal.current(), idempotencyKey),
                        TraceContext.current()));
    }

    /** 契约兼容入口：kbId 放在请求体中，行为与 /kbs/{kbId}/exports 完全一致。 */
    @PostMapping("/exports")
    @RequirePermission("export:create")
    public ResponseEntity<ApiEnvelope<ExportService.ExportView>> create(
            @RequestBody ExportService.CreateExportRequest request,
            @RequestHeader(value = "Idempotency-Key", required = false) String idempotencyKey) {
        if (request == null || request.kbId() == null || request.kbId().isBlank()) {
            throw new ApiException(ErrorCodes.PARAM_MISSING, HttpStatus.BAD_REQUEST, "kbId 不能为空");
        }
        return ResponseEntity.status(HttpStatus.ACCEPTED)
                .body(ApiEnvelope.ok(service.create(request.kbId(), request, SecurityPrincipal.current(),
                        idempotencyKey), TraceContext.current()));
    }

    @GetMapping("/exports/{exportId}")
    @RequirePermission("export:read")
    public ApiEnvelope<ExportService.ExportView> get(@PathVariable String exportId) {
        return ApiEnvelope.ok(service.get(exportId, SecurityPrincipal.current()), TraceContext.current());
    }

    @GetMapping("/exports/{exportId}/download")
    @RequirePermission("export:download")
    public ResponseEntity<byte[]> download(@PathVariable String exportId) {
        ExportService.Download download = service.download(exportId, SecurityPrincipal.current());
        String encoded = URLEncoder.encode(download.fileName(), StandardCharsets.UTF_8).replace("+", "%20");
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.parseMediaType(download.contentType()));
        headers.set(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename*=UTF-8''" + encoded);
        headers.setContentLength(download.content().length);
        headers.setCacheControl(CacheControl.noStore());
        headers.set("X-Content-SHA256", download.fileHash());
        headers.set("X-Trace-Id", TraceContext.current());
        return new ResponseEntity<>(download.content(), headers, HttpStatus.OK);
    }

    @PostMapping("/exports/{exportId}:retry")
    @RequirePermission("export:create")
    public ApiEnvelope<ExportService.ExportView> retry(@PathVariable String exportId) {
        return ApiEnvelope.ok(service.retry(exportId, SecurityPrincipal.current()), TraceContext.current());
    }
}
