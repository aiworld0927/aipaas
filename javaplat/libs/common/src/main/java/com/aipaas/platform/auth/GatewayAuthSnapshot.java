package com.aipaas.platform.auth;

import java.util.List;
import java.util.Set;

/** 管理服务发布到 Redis 的网关安全快照；不包含密码、令牌或业务数据。 */
public record GatewayAuthSnapshot(String userId, String tenantId, String username, String displayName,
                                  String sessionId, String status, long authVersion,
                                  Set<String> roles, Set<String> permissions, List<String> kbIds) {

    public SecurityPrincipal toPrincipal(String tokenSessionId) {
        String actualSession = tokenSessionId == null || tokenSessionId.isBlank() ? sessionId : tokenSessionId;
        return new SecurityPrincipal(userId, tenantId, username, displayName, actualSession, roles, permissions);
    }
}
