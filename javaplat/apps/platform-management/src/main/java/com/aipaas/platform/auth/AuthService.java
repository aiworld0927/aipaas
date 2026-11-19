package com.aipaas.platform.auth;

import com.aipaas.platform.audit.AuditService;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.crypto.EncryptionService;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.Base64;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Service
public class AuthService {

    private static final SecureRandom RANDOM = new SecureRandom();
    private final UserRepository users;
    private final PasswordEncoder encoder;
    private final PasswordPolicy passwordPolicy;
    private final JwtService jwt;
    private final AuditService audit;
    private final EncryptionService encryption;
    private final GatewayAuthStatePublisher gatewayState;
    private final int maxFailedAttempts;
    private final Duration lockDuration;
    private final Duration refreshTtl;
    private final String dummyHash;

    public AuthService(UserRepository users, PasswordEncoder encoder, PasswordPolicy passwordPolicy, JwtService jwt,
                       AuditService audit, EncryptionService encryption, GatewayAuthStatePublisher gatewayState,
                       @Value("${aipaas.security.max-failed-attempts:5}") int maxFailedAttempts,
                       @Value("${aipaas.security.lock-duration:PT15M}") Duration lockDuration,
                       @Value("${aipaas.security.refresh-token-ttl:P7D}") Duration refreshTtl) {
        this.users = users;
        this.encoder = encoder;
        this.passwordPolicy = passwordPolicy;
        this.jwt = jwt;
        this.audit = audit;
        this.encryption = encryption;
        this.gatewayState = gatewayState;
        this.maxFailedAttempts = maxFailedAttempts;
        this.lockDuration = lockDuration;
        this.refreshTtl = refreshTtl;
        this.dummyHash = encoder.encode("Not-A-User-Password!");
    }

    public Map<String, Object> login(String username, String password, String tenantId, String ip, String userAgent,
                                     String traceId) {
        String tenant = tenantId == null || tenantId.isBlank() ? "default" : tenantId;
        UserAccount found = users.findByUsername(tenant, username).orElse(null);
        if (found != null) {
            users.unlockIfExpired(found);
            found = users.findById(found.id()).orElseThrow();
        }
        String hash = found == null ? dummyHash : found.passwordHash();
        boolean matches = encoder.matches(password, hash);
        if (found == null || !matches || !"ACTIVE".equals(found.status())) {
            return failedLogin(found, tenant, username, ip, userAgent, traceId);
        }
        users.recordSuccess(found.id());
        UserAccount active = users.findById(found.id()).orElseThrow();
        Map<String, Object> response = issueSession(active, null, null);
        audit.record(principal(active, "session-pending"), "auth.login", "user", active.id(), "SUCCESS",
                Map.of("username", active.username()), ip, userAgent, traceId);
        return response;
    }

    private Map<String, Object> failedLogin(UserAccount found, String tenant, String username, String ip,
                                            String userAgent, String traceId) {
        String reason = "invalid_credentials";
        if (found != null) {
            if ("DISABLED".equals(found.status())) {
                reason = "account_disabled";
            } else if ("LOCKED".equals(found.status()) && found.lockedUntil() != null
                        && found.lockedUntil().isAfter(UserRepository.now())) {
                reason = "account_locked";
            } else {
                boolean locking = found.failedAttempts() + 1 >= maxFailedAttempts;
                if (locking) gatewayState.invalidateUser(found.id());
                users.recordFailure(found.id(), maxFailedAttempts, lockDuration);
                if (locking) {
                    gatewayState.publishUser(found.id(), null);
                    reason = "account_locked";
                }
            }
        }
        SecurityPrincipal actor = found == null
                ? new SecurityPrincipal("anonymous", tenant, username, username, "none", Set.of(), Set.of())
                : principal(found, "none");
        audit.record(actor, "auth.login.failed", "user", found == null ? username : found.id(), "FAILURE",
                Map.of("reason", reason), ip, userAgent, traceId);
        if ("account_locked".equals(reason)) {
            throw new ApiException(ErrorCodes.AUTH_ACCOUNT_LOCKED, HttpStatus.UNAUTHORIZED, "账号已临时锁定");
        }
        throw new ApiException(ErrorCodes.AUTH_INVALID_CREDENTIALS, HttpStatus.UNAUTHORIZED, "用户名或密码错误");
    }

    public Map<String, Object> refresh(String rawToken, String ip, String userAgent, String traceId) {
        String hash = hashToken(rawToken);
        UserRepository.RefreshTokenRecord stored = users.findRefreshToken(hash).orElseGet(() -> {
            SecurityPrincipal anonymous = new SecurityPrincipal("anonymous", "default", "anonymous", "anonymous",
                    "unknown", Set.of(), Set.of());
            audit.record(anonymous, "auth.token.refresh.failed", "session", "unknown", "FAILURE",
                    Map.of("reason", "refresh_token_invalid"), ip, userAgent, traceId);
            throw new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED, HttpStatus.UNAUTHORIZED, "刷新令牌无效");
        });
        UserAccount user = users.findById(stored.userId()).orElse(null);
        if (stored.revokedAt() != null) {
            users.revokeSessionRefreshTokens(stored.sessionId());
            auditRefreshFailure(user, stored, "refresh_token_reused", ip, userAgent, traceId);
            throw new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED, HttpStatus.UNAUTHORIZED, "刷新令牌已失效");
        }
        if (!stored.expiresAt().isAfter(UserRepository.now()) || user == null || !"ACTIVE".equals(user.status())) {
            users.revokeSessionRefreshTokens(stored.sessionId());
            auditRefreshFailure(user, stored, "refresh_token_expired_or_user_inactive", ip, userAgent, traceId);
            throw new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED, HttpStatus.UNAUTHORIZED, "刷新令牌已失效");
        }
        String newTokenId = newId("rtk_");
        if (!users.revokeRefreshToken(hash, newTokenId)) {
            users.revokeSessionRefreshTokens(stored.sessionId());
            auditRefreshFailure(user, stored, "refresh_token_reused", ip, userAgent, traceId);
            throw new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED, HttpStatus.UNAUTHORIZED, "刷新令牌已失效");
        }
        Map<String, Object> response = issueSession(user, stored.sessionId(), newTokenId);
        audit.record(principal(user, stored.sessionId()), "auth.token.refresh", "session", stored.sessionId(), "SUCCESS",
                Map.of("rotated", true), ip, userAgent, traceId);
        return response;
    }

    private void auditRefreshFailure(UserAccount user, UserRepository.RefreshTokenRecord stored, String reason,
                                     String ip, String userAgent, String traceId) {
        SecurityPrincipal actor = user == null
                ? new SecurityPrincipal("anonymous", stored.tenantId(), "anonymous", "anonymous", stored.sessionId(), Set.of(), Set.of())
                : principal(user, stored.sessionId());
        audit.record(actor, "auth.token.refresh.failed", "session", stored.sessionId(), "FAILURE", Map.of("reason", reason),
                ip, userAgent, traceId);
    }

    public void logout(SecurityPrincipal principal, String jti, OffsetDateTime expiresAt, String ip,
                       String userAgent, String traceId) {
        if (jti != null && expiresAt != null) {
            // 先发布撤销，再写数据库；发布失败时旧令牌不会继续被网关接受。
            gatewayState.revoke(jti, expiresAt);
            users.revokeAccessToken(jti, expiresAt);
        }
        users.revokeSessionRefreshTokens(principal.sessionId());
        audit.record(principal, "auth.logout", "session", principal.sessionId(), "SUCCESS", Map.of(), ip, userAgent, traceId);
    }

    @Transactional
    public Map<String, Object> createUser(String tenantId, String username, String displayName, String password,
                                          String email, java.util.List<String> roleCodes) {
        try {
            passwordPolicy.validate(password, username);
        } catch (IllegalArgumentException ex) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, ex.getMessage());
        }
        if (!username.matches("[A-Za-z0-9_.-]{3,64}") || displayName == null || displayName.isBlank()
                || displayName.length() > 64 || roleCodes == null || roleCodes.isEmpty()) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "用户资料不合法");
        }
        if (users.findByUsername(tenantId, username).isPresent()) {
            throw new ApiException(ErrorCodes.BIZ_DUPLICATE, HttpStatus.CONFLICT, "用户名已存在");
        }
        String id = newId("usr_");
        OffsetDateTime now = UserRepository.now();
        String encryptedEmail = email == null || email.isBlank() ? null : encryption.encrypt(email, id + ":email");
        UserAccount account = new UserAccount(id, tenantId, username, displayName, encryptedEmail,
                encoder.encode(password), "ACTIVE", 0, null, 1, null, now, now);
        users.create(account);
        roleCodes.stream().distinct().forEach(code -> users.assignRole(tenantId, id, code));
        gatewayState.publishUserAfterCommit(id);
        return userResponse(account, roleCodes);
    }

    public SecurityPrincipal principal(UserAccount user, String sessionId) {
        return new SecurityPrincipal(user.id(), user.tenantId(), user.username(), user.displayName(), sessionId,
                users.roleCodes(user.id()), users.permissionCodes(user.id()));
    }

    public SecurityPrincipal principal(UserAccount user) {
        return principal(user, "session-pending");
    }

    private Map<String, Object> issueSession(UserAccount user, String sessionId, String refreshId) {
        String actualSessionId = sessionId == null ? newId("ses_") : sessionId;
        SecurityPrincipal principal = principal(user, actualSessionId);
        JwtService.IssuedAccessToken access = jwt.issue(user, actualSessionId, principal.roles(), principal.permissions());
        String refreshToken = randomToken();
        String tokenId = refreshId == null ? newId("rtk_") : refreshId;
        users.saveRefreshToken(hashToken(refreshToken), tokenId, user.tenantId(), user.id(), actualSessionId,
                UserRepository.now().plus(refreshTtl));
        gatewayState.publishUser(user.id(), actualSessionId);
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("accessToken", access.token());
        data.put("refreshToken", refreshToken);
        data.put("tokenType", "Bearer");
        data.put("expiresIn", access.expiresIn());
        data.put("refreshExpiresIn", refreshTtl.toSeconds());
        data.put("user", principal.toCurrentUser());
        return data;
    }

    private Map<String, Object> userResponse(UserAccount user, java.util.List<String> roles) {
        java.util.LinkedHashMap<String, Object> data = new java.util.LinkedHashMap<>();
        data.put("id", user.id());
        data.put("tenantId", user.tenantId());
        data.put("username", user.username());
        data.put("displayName", user.displayName());
        if (user.emailCiphertext() != null) data.put("email", encryption.decrypt(user.emailCiphertext(), user.id() + ":email"));
        data.put("status", user.status());
        data.put("roles", roles);
        data.put("createdAt", user.createdAt());
        data.put("updatedAt", user.updatedAt());
        return data;
    }

    public static String hashToken(String token) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(token.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException ex) {
            throw new IllegalStateException(ex);
        }
    }

    private static String randomToken() {
        byte[] token = new byte[48];
        RANDOM.nextBytes(token);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(token);
    }

    private static String newId(String prefix) {
        return prefix + UUID.randomUUID().toString().replace("-", "");
    }
}
