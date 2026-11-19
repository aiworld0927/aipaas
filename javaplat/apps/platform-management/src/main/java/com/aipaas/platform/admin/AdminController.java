package com.aipaas.platform.admin;

import com.aipaas.platform.auth.AuthService;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.rbac.RequirePermission;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/** 基础管理后台（#193）：用户、系统配置入口与总览；写操作由 AuditFilter 留痕。 */
@RestController
@RequestMapping("/api/v1/admin")
public class AdminController {

    private final AdminService admin;
    private final AuthService auth;

    public AdminController(AdminService admin, AuthService auth) {
        this.admin = admin;
        this.auth = auth;
    }

    @GetMapping("/users")
    @RequirePermission("user:read")
    public ApiEnvelope<?> listUsers(@RequestParam(defaultValue = "1") int page,
                                    @RequestParam(defaultValue = "20") int pageSize,
                                    @RequestParam(required = false) String keyword,
                                    @RequestParam(required = false) String status) {
        validatePage(page, pageSize);
        return ApiEnvelope.ok(admin.listUsers(SecurityPrincipal.current().tenantId(), page, pageSize, keyword, status),
                TraceContext.current());
    }

    @PostMapping("/users")
    @ResponseStatus(HttpStatus.CREATED)
    @RequirePermission("user:write")
    public ApiEnvelope<?> createUser(@Valid @RequestBody CreateUserRequest request) {
        return ApiEnvelope.ok(auth.createUser(SecurityPrincipal.current().tenantId(), request.username(),
                request.displayName(), request.password(), request.email(), request.roleCodes()), TraceContext.current());
    }

    @PatchMapping("/users/{userId}")
    @RequirePermission("user:write")
    public ApiEnvelope<?> updateUser(@PathVariable String userId, @Valid @RequestBody UserUpdateRequest request) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(admin.updateUser(principal.tenantId(), principal.userId(), userId,
                request.displayName(), request.email(), request.status(), request.roleCodes()), TraceContext.current());
    }

    @GetMapping("/system-config")
    @RequirePermission("config:read")
    public ApiEnvelope<?> getSystemConfig() {
        return ApiEnvelope.ok(admin.systemConfig(SecurityPrincipal.current().tenantId()), TraceContext.current());
    }

    @PutMapping("/system-config")
    @RequirePermission("config:write")
    public ApiEnvelope<?> updateSystemConfig(@Valid @RequestBody SystemConfigUpdateRequest request) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        return ApiEnvelope.ok(admin.updateSystemConfig(principal.tenantId(), principal.userId(),
                request.defaultCapability(), request.maxUploadSizeBytes(), request.maxQueryLength(),
                request.streamingEnabled(), request.retrievalTopK()), TraceContext.current());
    }

    @GetMapping("/overview")
    @RequirePermission("admin:read")
    public ApiEnvelope<?> overview() {
        return ApiEnvelope.ok(admin.overview(SecurityPrincipal.current().tenantId()), TraceContext.current());
    }

    private static void validatePage(int page, int pageSize) {
        if (page < 1 || pageSize < 1 || pageSize > 100) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "分页参数不合法");
        }
    }

    public record CreateUserRequest(@Size(min = 3, max = 64) @NotBlank String username,
                                    @NotBlank @Size(max = 64) String displayName,
                                    @NotBlank @Size(min = 8, max = 128) String password,
                                    @Email String email, @NotNull List<String> roleCodes) {
    }

    public record UserUpdateRequest(@Size(min = 1, max = 64) String displayName,
                                    @Email @Size(max = 128) String email,
                                    String status,
                                    List<String> roleCodes) {
    }

    public record SystemConfigUpdateRequest(@Size(max = 16) String defaultCapability,
                                            @Min(1) @Max(104857600) Long maxUploadSizeBytes,
                                            @Min(1) @Max(8000) Integer maxQueryLength,
                                            Boolean streamingEnabled,
                                            @Min(1) @Max(50) Integer retrievalTopK) {
    }
}
