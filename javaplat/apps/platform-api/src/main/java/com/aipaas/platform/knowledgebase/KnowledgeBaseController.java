package com.aipaas.platform.knowledgebase;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.rbac.RequirePermission;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/v1/knowledge-bases")
public class KnowledgeBaseController {

    private final KnowledgeBaseService service;

    public KnowledgeBaseController(KnowledgeBaseService service) {
        this.service = service;
    }

    @GetMapping
    @RequirePermission("kb:read")
    public ApiEnvelope<?> list(@RequestParam(defaultValue = "1") int page,
                               @RequestParam(defaultValue = "20") int pageSize,
                               @RequestParam(required = false) String keyword,
                               @RequestParam(required = false) String domain) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(service.list(principal.tenantId(), page, pageSize, keyword, domain),
                TraceContext.current());
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    @RequirePermission("kb:write")
    public ApiEnvelope<?> create(@Valid @RequestBody KnowledgeBaseRequest request) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(service.create(principal.tenantId(), principal.userId(), request.name(),
                request.description(), request.domain(), request.standardSystem(), request.config()),
                TraceContext.current());
    }

    @GetMapping("/{kbId}")
    @RequirePermission("kb:read")
    public ApiEnvelope<?> get(@PathVariable String kbId) {
        return ApiEnvelope.ok(service.get(SecurityPrincipal.current().tenantId(), kbId), TraceContext.current());
    }

    @PatchMapping("/{kbId}")
    @RequirePermission("kb:write")
    public ApiEnvelope<?> update(@PathVariable String kbId, @Valid @RequestBody KnowledgeBaseUpdateRequest request) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(service.update(principal.tenantId(), kbId, request.name(), request.description(),
                request.domain(), request.standardSystem(), request.config()), TraceContext.current());
    }

    @DeleteMapping("/{kbId}")
    @RequirePermission("kb:write")
    public ApiEnvelope<?> delete(@PathVariable String kbId) {
        return ApiEnvelope.ok(service.delete(SecurityPrincipal.current().tenantId(), kbId), TraceContext.current());
    }

    @GetMapping("/{kbId}/index-status")
    @RequirePermission("kb:read")
    public ApiEnvelope<?> indexStatus(@PathVariable String kbId) {
        return ApiEnvelope.ok(service.indexStatus(SecurityPrincipal.current().tenantId(), kbId),
                TraceContext.current());
    }

    public record KnowledgeBaseRequest(@NotBlank @Size(max = 128) String name,
                                       @Size(max = 1024) String description,
                                       @Size(max = 64) String domain,
                                       @Size(max = 128) String standardSystem,
                                       Map<String, Object> config) {
    }

    public record KnowledgeBaseUpdateRequest(@Size(min = 1, max = 128) String name,
                                             @Size(max = 1024) String description,
                                             @Size(max = 64) String domain,
                                             @Size(max = 128) String standardSystem,
                                             Map<String, Object> config) {
    }
}
