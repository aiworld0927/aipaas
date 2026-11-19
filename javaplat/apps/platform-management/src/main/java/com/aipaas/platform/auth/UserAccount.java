package com.aipaas.platform.auth;

import java.time.OffsetDateTime;

public record UserAccount(String id, String tenantId, String username, String displayName,
                          String emailCiphertext, String passwordHash, String status,
                          int failedAttempts, OffsetDateTime lockedUntil, long authVersion,
                          OffsetDateTime lastLoginAt, OffsetDateTime createdAt, OffsetDateTime updatedAt) {
}
