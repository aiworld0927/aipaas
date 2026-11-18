package com.aipaas.platform.rbac;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.auth.UserRepository;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.api.PageResult;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Service
public class RbacService {

    private final RoleRepository roles;
    private final UserRepository users;

    public RbacService(RoleRepository roles, UserRepository users) {
        this.roles = roles;
        this.users = users;
    }

    /** 不使用 JWT 权限副本或缓存；角色和 KB 授权变更在下一请求立即生效。 */
    public boolean allowed(SecurityPrincipal principal, String permission, String kbId) {
        Set<String> permissions = users.permissionCodes(principal.userId());
        if (permissions.contains("*")) return true;
        if (kbId == null || kbId.isBlank()) return permissions.contains(permission);
        if (!permissions.contains(permission) && !permissions.contains("kb:manage")) return false;
        return users.hasKbPermission(principal.userId(), kbId, permission);
    }

    public Map<String, Object> permissions(String tenantId) {
        return Map.of("items", roles.permissions(tenantId));
    }

    public PageResult<Map<String, Object>> roles(String tenantId, int page, int pageSize) {
        List<Map<String, Object>> items = roles.pageRoles(tenantId, page, pageSize).stream().map(this::toResponse).toList();
        return PageResult.of(items, roles.countRoles(tenantId), page, pageSize);
    }

    @Transactional
    public Map<String, Object> createRole(String tenantId, String code, String name,
                                          List<String> permissions, List<RoleRepository.Grant> grants) {
        if (!code.matches("[A-Z][A-Z0-9_]{2,31}") || name == null || name.isBlank() || name.length() > 64) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "角色编码或名称不合法");
        }
        if (roles.findByCode(tenantId, code).isPresent()) {
            throw new ApiException(ErrorCodes.BIZ_DUPLICATE, HttpStatus.CONFLICT, "角色编码已存在");
        }
        validateMatrix(tenantId, permissions, grants);
        String id = "rol_" + UUID.randomUUID().toString().replace("-", "");
        roles.createRole(id, tenantId, code, name, false);
        roles.replaceMatrix(tenantId, id, distinct(permissions), grants);
        return toResponse(roles.findById(tenantId, id).orElseThrow());
    }

    @Transactional
    public Map<String, Object> updateMatrix(String tenantId, String roleId,
                                            List<String> permissions, List<RoleRepository.Grant> grants) {
        RoleRepository.RoleView current = roles.findById(tenantId, roleId)
                .orElseThrow(() -> ApiException.notFound("角色不存在"));
        if ("PLATFORM_ADMIN".equals(current.code())) {
            throw new ApiException(ErrorCodes.BIZ_CONFLICT, HttpStatus.CONFLICT, "内置平台管理员角色不可降权");
        }
        validateMatrix(tenantId, permissions, grants);
        roles.replaceMatrix(tenantId, roleId, distinct(permissions), grants);
        return toResponse(roles.findById(tenantId, roleId).orElseThrow());
    }

    public Map<String, Object> toResponse(RoleRepository.RoleView role) {
        java.util.LinkedHashMap<String, Object> result = new java.util.LinkedHashMap<>();
        result.put("id", role.id());
        result.put("tenantId", role.tenantId());
        result.put("code", role.code());
        result.put("name", role.name());
        result.put("builtIn", role.builtIn());
        result.put("permissions", role.permissions());
        result.put("grants", role.grants());
        result.put("userCount", role.userCount());
        result.put("createdAt", role.createdAt());
        result.put("updatedAt", role.updatedAt());
        return result;
    }

    private void validateMatrix(String tenantId, List<String> permissions, List<RoleRepository.Grant> grants) {
        if (permissions == null || grants == null || permissions.stream().anyMatch(p -> !roles.permissionExists(tenantId, p))) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "权限点不存在或 grants 缺失");
        }
        Set<String> permissionSet = new LinkedHashSet<>(permissions);
        for (RoleRepository.Grant grant : grants) {
            if (!"KB".equals(grant.scopeType())) {
                throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "P0 仅支持 KB scope");
            }
            if (grant.scopeId() == null || grant.scopeId().isBlank() || !permissionSet.contains(grant.permission())
                    || !roles.permissionExists(tenantId, grant.permission())) {
                throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "知识库授权与角色权限矩阵不一致");
            }
        }
    }

    private static List<String> distinct(List<String> values) {
        return new LinkedHashSet<>(values).stream().toList();
    }
}
