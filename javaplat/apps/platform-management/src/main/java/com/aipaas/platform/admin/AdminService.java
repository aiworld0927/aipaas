package com.aipaas.platform.admin;

import com.aipaas.platform.auth.GatewayAuthStatePublisher;
import com.aipaas.platform.auth.UserAccount;
import com.aipaas.platform.auth.UserRepository;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.api.PageResult;
import com.aipaas.platform.crypto.EncryptionService;
import com.aipaas.platform.rbac.RoleRepository;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** 管理后台（#193）：用户检索/维护、系统配置入口与总览；权限由 {@code @RequirePermission} 前置校验。 */
@Service
public class AdminService {

    private static final Set<String> USER_STATUSES = Set.of("ACTIVE", "DISABLED", "LOCKED");

    private final UserRepository users;
    private final RoleRepository roles;
    private final EncryptionService encryption;
    private final SystemConfigService systemConfig;
    private final JdbcTemplate jdbc;
    private final GatewayAuthStatePublisher gatewayState;
    private final Duration lockDuration;

    public AdminService(UserRepository users, RoleRepository roles, EncryptionService encryption,
                        SystemConfigService systemConfig, JdbcTemplate jdbc, GatewayAuthStatePublisher gatewayState,
                        @Value("${aipaas.security.lock-duration:PT15M}") Duration lockDuration) {
        this.users = users;
        this.roles = roles;
        this.encryption = encryption;
        this.systemConfig = systemConfig;
        this.jdbc = jdbc;
        this.gatewayState = gatewayState;
        this.lockDuration = lockDuration;
    }

    public PageResult<Map<String, Object>> listUsers(String tenantId, int page, int pageSize, String keyword,
                                                     String status) {
        validateStatus(status);
        long total = users.countUsers(tenantId, keyword, status);
        List<Map<String, Object>> items = users.page(tenantId, keyword, status, pageSize, (long) (page - 1) * pageSize)
                .stream().map(this::userView).toList();
        return PageResult.of(items, total, page, pageSize);
    }

    @Transactional
    public Map<String, Object> updateUser(String tenantId, String actorUserId, String userId, String displayName,
                                          String email, String status, List<String> roleCodes) {
        UserAccount current = users.findById(userId)
                .filter(user -> tenantId.equals(user.tenantId()))
                .orElseThrow(() -> ApiException.notFound("用户不存在"));
        String finalStatus = status == null ? current.status() : status;
        validateStatus(finalStatus);
        if (userId.equals(actorUserId) && !"ACTIVE".equals(finalStatus)) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "不能停用或锁定当前登录账号");
        }
        String finalDisplayName = displayName == null ? current.displayName() : displayName.trim();
        if (finalDisplayName.isEmpty() || finalDisplayName.length() > 64) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "显示名称长度需为 1..64");
        }
        String emailCiphertext = current.emailCiphertext();
        if (email != null) {
            emailCiphertext = email.isBlank() ? null : encryption.encrypt(email.trim(), userId + ":email");
        }
        List<String> finalRoles = roleCodes == null ? null : roleCodes.stream().distinct().toList();
        if (finalRoles != null) {
            for (String code : finalRoles) {
                if (roles.findByCode(tenantId, code).isEmpty()) {
                    throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "角色不存在: " + code);
                }
            }
        }
        OffsetDateTime lockedUntil = "LOCKED".equals(finalStatus)
                ? current.lockedUntil() != null && current.lockedUntil().isAfter(UserRepository.now())
                        ? current.lockedUntil() : UserRepository.now().plus(lockDuration)
                : null;
        gatewayState.invalidateUser(userId);
        users.updateProfile(userId, finalDisplayName, emailCiphertext, finalStatus, lockedUntil);
        if (finalRoles != null) {
            users.replaceRoles(tenantId, userId, finalRoles);
        }
        Map<String, Object> response = userView(users.findById(userId).orElseThrow());
        gatewayState.publishUserAfterCommit(userId);
        return response;
    }

    public Map<String, Object> systemConfig(String tenantId) {
        return systemConfig.get(tenantId);
    }

    public Map<String, Object> updateSystemConfig(String tenantId, String actorId, String defaultCapability,
                                                  Long maxUploadSizeBytes, Integer maxQueryLength,
                                                  Boolean streamingEnabled, Integer retrievalTopK) {
        return systemConfig.update(tenantId, actorId, defaultCapability, maxUploadSizeBytes, maxQueryLength,
                streamingEnabled, retrievalTopK);
    }

    public Map<String, Object> overview(String tenantId) {
        Map<String, Object> row = jdbc.queryForMap("select "
                        + "(select count(*) from knowledge_bases where tenant_id=? and deleted_at is null and status<>'DELETED') as kb_count, "
                        + "(select count(*) from documents where tenant_id=? and deleted_at is null) as document_count, "
                        + "(select coalesce(sum(chunk_count),0) from documents where tenant_id=? and deleted_at is null) as chunk_count",
                tenantId, tenantId, tenantId);
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("kbCount", ((Number) row.get("kb_count")).longValue());
        data.put("documentCount", ((Number) row.get("document_count")).longValue());
        data.put("chunkCount", ((Number) row.get("chunk_count")).longValue());
        data.put("userCount", users.countAllUsers(tenantId));
        // P0 未接指标聚合，先返回中性值；P1 可观测域接入后替换。
        data.put("qps", 0.0d);
        data.put("latencyP95Ms", 0);
        data.put("errorRate", 0.0d);
        data.put("updatedAt", OffsetDateTime.now(ZoneOffset.UTC));
        return data;
    }

    private Map<String, Object> userView(UserAccount user) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("id", user.id());
        data.put("tenantId", user.tenantId());
        data.put("username", user.username());
        data.put("displayName", user.displayName());
        if (user.emailCiphertext() != null) {
            data.put("email", encryption.decrypt(user.emailCiphertext(), user.id() + ":email"));
        }
        data.put("status", user.status());
        data.put("roles", users.roleCodes(user.id()).stream().sorted().toList());
        data.put("lastLoginAt", user.lastLoginAt());
        data.put("createdAt", user.createdAt());
        data.put("updatedAt", user.updatedAt());
        return data;
    }

    private static void validateStatus(String status) {
        if (status != null && !status.isBlank() && !USER_STATUSES.contains(status)) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "用户状态取值不合法");
        }
    }
}
