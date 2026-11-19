package com.aipaas.platform.auth;

import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.Map;
import java.util.Set;

public record SecurityPrincipal(String userId, String tenantId, String username, String displayName,
                                String sessionId, Set<String> roles, Set<String> permissions) {

    public static SecurityPrincipal current() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication != null && authentication.getPrincipal() instanceof SecurityPrincipal principal) {
            return principal;
        }
        return new SecurityPrincipal("anonymous", "default", "anonymous", "anonymous", "none", Set.of(), Set.of());
    }

    public Map<String, Object> toCurrentUser() {
        return Map.of("userId", userId, "tenantId", tenantId, "username", username,
                "displayName", displayName, "roles", roles.stream().sorted().toList(),
                "permissions", permissions.stream().sorted().toList(), "defaultTenant", "default".equals(tenantId));
    }
}
