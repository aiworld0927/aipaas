package com.aipaas.platform.tenant;

import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.rbac.RequirePermission;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/** 多租户管理：租户台账维护，新租户自动复制权限目录与内置角色。 */
@RestController
@RequestMapping("/api/v1/admin/tenants")
public class TenantController {

    private final TenantService tenants;

    public TenantController(TenantService tenants) {
        this.tenants = tenants;
    }

    @GetMapping
    @RequirePermission("tenant:read")
    public ApiEnvelope<Map<String, Object>> list() {
        return ApiEnvelope.ok(tenants.list(), TraceContext.current());
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    @RequirePermission("tenant:write")
    public ApiEnvelope<Map<String, Object>> create(@RequestBody CreateTenantRequest request) {
        return ApiEnvelope.ok(tenants.create(request.id(), request.name(), request.description()),
                TraceContext.current());
    }

    @PatchMapping("/{tenantId}")
    @RequirePermission("tenant:write")
    public ApiEnvelope<Map<String, Object>> update(@PathVariable String tenantId,
                                                   @RequestBody UpdateTenantRequest request) {
        return ApiEnvelope.ok(tenants.update(tenantId, request.name(), request.status(), request.description()),
                TraceContext.current());
    }

    public record CreateTenantRequest(@NotBlank @Size(min = 2, max = 32) String id,
                                      @NotBlank @Size(max = 128) String name,
                                      @Size(max = 512) String description) {
    }

    public record UpdateTenantRequest(@Size(max = 128) String name, String status,
                                      @Size(max = 512) String description) {
    }
}
