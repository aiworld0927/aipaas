package com.aipaas.platform.rbac;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/v1")
public class RbacController {

    private final RbacService rbac;

    public RbacController(RbacService rbac) {
        this.rbac = rbac;
    }

    @GetMapping("/permissions")
    @RequirePermission("role:read")
    public ApiEnvelope<Map<String, Object>> permissions() {
        return ApiEnvelope.ok(rbac.permissions(SecurityPrincipal.current().tenantId()), TraceContext.current());
    }

    @GetMapping("/roles")
    @RequirePermission("role:read")
    public ApiEnvelope<?> roles(@RequestParam(defaultValue = "1") int page,
                                @RequestParam(defaultValue = "20") int pageSize) {
        validatePage(page, pageSize);
        return ApiEnvelope.ok(rbac.roles(SecurityPrincipal.current().tenantId(), page, pageSize), TraceContext.current());
    }

    @PostMapping("/roles")
    @ResponseStatus(HttpStatus.CREATED)
    @RequirePermission("role:write")
    public ApiEnvelope<Map<String, Object>> create(@Valid @RequestBody RoleCreateRequest request) {
        return ApiEnvelope.ok(rbac.createRole(SecurityPrincipal.current().tenantId(), request.code(), request.name(),
                request.permissions(), request.grants()), TraceContext.current());
    }

    @PutMapping("/roles/{roleId}/permissions")
    @RequirePermission("role:write")
    public ApiEnvelope<Map<String, Object>> update(@PathVariable String roleId,
                                                   @Valid @RequestBody RolePermissionUpdateRequest request) {
        return ApiEnvelope.ok(rbac.updateMatrix(SecurityPrincipal.current().tenantId(), roleId,
                request.permissions(), request.grants()), TraceContext.current());
    }

    private static void validatePage(int page, int pageSize) {
        if (page < 1 || pageSize < 1 || pageSize > 100) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "分页参数不合法");
        }
    }

    public record RoleCreateRequest(@NotBlank String code, @NotBlank String name,
                                    @NotNull List<String> permissions, List<RoleRepository.Grant> grants) {
        public RoleCreateRequest {
            if (grants == null) grants = List.of();
        }
    }

    public record RolePermissionUpdateRequest(@NotNull List<String> permissions, List<RoleRepository.Grant> grants) {
        public RolePermissionUpdateRequest {
            if (grants == null) grants = List.of();
        }
    }
}
