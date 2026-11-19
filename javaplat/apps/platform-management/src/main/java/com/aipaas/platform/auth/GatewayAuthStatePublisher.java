package com.aipaas.platform.auth;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.List;

/** 数据库认证事实源到 Redis 网关快照的同步发布器。 */
@Component
public class GatewayAuthStatePublisher {

    public static final String USER_KEY_PREFIX = "aipaas:gateway:auth:";
    public static final String REVOKED_KEY_PREFIX = "aipaas:gateway:revoked:";
    private static final Logger log = LoggerFactory.getLogger(GatewayAuthStatePublisher.class);

    private final UserRepository users;
    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;

    public GatewayAuthStatePublisher(UserRepository users, StringRedisTemplate redis, ObjectMapper objectMapper) {
        this.users = users;
        this.redis = redis;
        this.objectMapper = objectMapper;
    }

    public void publishUser(String userId, String sessionId) {
        UserAccount user = users.findById(userId).orElse(null);
        if (user == null) {
            invalidateUser(userId);
            return;
        }
        GatewayAuthSnapshot snapshot = new GatewayAuthSnapshot(user.id(), user.tenantId(), user.username(),
                user.displayName(), sessionId, user.status(), user.authVersion(), users.roleCodes(userId),
                users.permissionCodes(userId), users.permittedKbIds(userId, "retrieval:query"));
        try {
            redis.opsForValue().set(USER_KEY_PREFIX + userId, objectMapper.writeValueAsString(snapshot));
        } catch (com.fasterxml.jackson.core.JsonProcessingException ex) {
            throw new IllegalStateException("Unable to serialize gateway authentication snapshot", ex);
        }
    }

    public void invalidateUser(String userId) {
        redis.delete(USER_KEY_PREFIX + userId);
    }

    public void invalidateUsers(List<String> userIds) {
        userIds.forEach(this::invalidateUser);
    }

    public void publishUserAfterCommit(String userId) {
        afterCommit(() -> publishUser(userId, null));
    }

    public void publishUsersAfterCommit(List<String> userIds) {
        afterCommit(() -> userIds.forEach(userId -> publishUser(userId, null)));
    }

    public void revoke(String jti, OffsetDateTime expiresAt) {
        Duration ttl = Duration.between(UserRepository.now(), expiresAt);
        if (ttl.isNegative() || ttl.isZero()) {
            redis.delete(REVOKED_KEY_PREFIX + jti);
            return;
        }
        redis.opsForValue().set(REVOKED_KEY_PREFIX + jti, "1", ttl);
    }

    public void publishAll() {
        users.allUsers().forEach(user -> publishUser(user.id(), null));
        users.activeRevokedTokens().forEach(token -> revoke(token.jti(), token.expiresAt()));
    }

    @EventListener(ApplicationReadyEvent.class)
    public void onReady() {
        syncSafely();
    }

    @Scheduled(fixedDelayString = "${aipaas.gateway-state.sync-interval-ms:60000}",
            initialDelayString = "${aipaas.gateway-state.sync-interval-ms:60000}")
    public void syncSafely() {
        try {
            publishAll();
        } catch (RuntimeException ex) {
            log.error("网关认证快照同步失败，缺失快照的用户将被网关拒绝", ex);
        }
    }

    private static void afterCommit(Runnable action) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    action.run();
                }
            });
        } else {
            action.run();
        }
    }
}
