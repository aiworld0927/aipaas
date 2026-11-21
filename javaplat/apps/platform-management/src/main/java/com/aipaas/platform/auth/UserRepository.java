package com.aipaas.platform.auth;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;

@Repository
public class UserRepository {

    private final JdbcTemplate jdbc;

    public UserRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private final RowMapper<UserAccount> mapper = (rs, rowNum) -> new UserAccount(
            rs.getString("id"), rs.getString("tenant_id"), rs.getString("username"),
            rs.getString("display_name"), rs.getString("email_ciphertext"), rs.getString("password_hash"),
            rs.getString("status"), rs.getInt("failed_attempts"),
            rs.getObject("locked_until", OffsetDateTime.class), rs.getLong("auth_version"),
            rs.getObject("last_login_at", OffsetDateTime.class), rs.getObject("created_at", OffsetDateTime.class),
            rs.getObject("updated_at", OffsetDateTime.class));

    public Optional<UserAccount> findByUsername(String tenantId, String username) {
        return jdbc.query("select * from sec_users where tenant_id=? and username=?", mapper, tenantId, username)
                .stream().findFirst();
    }

    public Optional<UserAccount> findById(String id) {
        return jdbc.query("select * from sec_users where id=?", mapper, id).stream().findFirst();
    }

    public List<UserAccount> allUsers() {
        return jdbc.query("select * from sec_users order by id", mapper);
    }

    public List<String> userIdsForRole(String tenantId, String roleId) {
        return jdbc.query("select user_id from sec_user_roles where tenant_id=? and role_id=? order by user_id",
                (rs, n) -> rs.getString(1), tenantId, roleId);
    }

    public Set<String> roleCodes(String userId) {
        return new HashSet<>(jdbc.query("select r.code from sec_roles r join sec_user_roles ur on ur.role_id=r.id "
                + "where ur.user_id=?", (rs, n) -> rs.getString(1), userId));
    }

    public Set<String> permissionCodes(String userId) {
        return new HashSet<>(jdbc.query("select distinct rp.permission_code from sec_role_permissions rp "
                + "join sec_user_roles ur on ur.role_id=rp.role_id and ur.tenant_id=rp.tenant_id "
                + "where ur.user_id=?", (rs, n) -> rs.getString(1), userId));
    }

    public boolean hasKbPermission(String userId, String kbId, String permission) {
        Integer count = jdbc.queryForObject("select count(*) from sec_role_kb_grants g "
                        + "join sec_user_roles ur on ur.role_id=g.role_id and ur.tenant_id=g.tenant_id "
                        + "where ur.user_id=? and g.kb_id=? and g.permission_code in (?, 'kb:manage')",
                Integer.class, userId, kbId, permission);
        return count != null && count > 0;
    }

    /** 用户被授权的知识库集合：供问答编排层以请求头下发给 Python 检索侧做前置过滤。 */
    public List<String> permittedKbIds(String userId, String permission) {
        return jdbc.query("select distinct g.kb_id from sec_role_kb_grants g "
                        + "join sec_user_roles ur on ur.role_id=g.role_id and ur.tenant_id=g.tenant_id "
                        + "where ur.user_id=? and g.permission_code in (?, 'kb:manage') order by g.kb_id",
                (rs, n) -> rs.getString(1), userId, permission);
    }

    public void recordFailure(String userId, int maxAttempts, Duration lockDuration) {
        OffsetDateTime now = now();
        OffsetDateTime lockUntil = now.plus(lockDuration);
        jdbc.update("update sec_users set failed_attempts=failed_attempts+1, "
                        + "status=case when failed_attempts+1>=? then 'LOCKED' else status end, "
                        + "locked_until=case when failed_attempts+1>=? then ? else locked_until end, updated_at=? where id=?",
                maxAttempts, maxAttempts, lockUntil, now, userId);
    }

    public void recordSuccess(String userId) {
        OffsetDateTime now = now();
        jdbc.update("update sec_users set failed_attempts=0, locked_until=null, status='ACTIVE', "
                + "last_login_at=?, updated_at=? where id=?", now, now, userId);
    }

    public void unlockIfExpired(UserAccount account) {
        if ("LOCKED".equals(account.status()) && account.lockedUntil() != null
                && !account.lockedUntil().isAfter(now())) {
            jdbc.update("update sec_users set status='ACTIVE', failed_attempts=0, locked_until=null, updated_at=? where id=?",
                    now(), account.id());
        }
    }

    public void create(UserAccount account) {
        jdbc.update("insert into sec_users(id,tenant_id,username,display_name,email_ciphertext,password_hash,status,"
                        + "failed_attempts,auth_version,created_at,updated_at) values(?,?,?,?,?,?,?,0,?,?,?)",
                account.id(), account.tenantId(), account.username(), account.displayName(), account.emailCiphertext(),
                account.passwordHash(), account.status(), account.authVersion(), account.createdAt(), account.updatedAt());
    }

    public boolean tenantEnabled(String tenantId) {
        List<String> statuses = jdbc.queryForList("select status from tenants where id=?", String.class, tenantId);
        // 迁移前或未登记租户按启用处理，避免影响既有账号。
        return statuses.isEmpty() || "ACTIVE".equals(statuses.get(0));
    }

    public boolean roleExists(String tenantId, String roleCode) {
        Integer count = jdbc.queryForObject("select count(*) from sec_roles where tenant_id=? and code=?",
                Integer.class, tenantId, roleCode);
        return count != null && count > 0;
    }

    public void assignRole(String tenantId, String userId, String roleCode) {
        List<String> ids = jdbc.query("select id from sec_roles where tenant_id=? and code=?",
                (rs, n) -> rs.getString(1), tenantId, roleCode);
        if (ids.size() != 1) {
            throw new IllegalArgumentException("角色不存在: " + roleCode);
        }
        jdbc.update("insert into sec_user_roles(tenant_id,user_id,role_id) values(?,?,?)", tenantId, userId, ids.getFirst());
    }

    public List<UserAccount> page(String tenantId, String keyword, String status, int limit, long offset) {
        StringBuilder sql = new StringBuilder("select * from sec_users where tenant_id=?");
        List<Object> args = new ArrayList<>();
        args.add(tenantId);
        appendFilters(sql, args, keyword, status);
        sql.append(" order by created_at desc limit ? offset ?");
        args.add(limit);
        args.add(offset);
        return jdbc.query(sql.toString(), mapper, args.toArray());
    }

    public long countUsers(String tenantId, String keyword, String status) {
        StringBuilder sql = new StringBuilder("select count(*) from sec_users where tenant_id=?");
        List<Object> args = new ArrayList<>();
        args.add(tenantId);
        appendFilters(sql, args, keyword, status);
        Long count = jdbc.queryForObject(sql.toString(), Long.class, args.toArray());
        return count == null ? 0 : count;
    }

    public long countAllUsers(String tenantId) {
        Long count = jdbc.queryForObject("select count(*) from sec_users where tenant_id=?", Long.class, tenantId);
        return count == null ? 0 : count;
    }

    public void updateProfile(String userId, String displayName, String emailCiphertext, String status,
                              OffsetDateTime lockedUntil) {
        jdbc.update("update sec_users set display_name=?, email_ciphertext=?, status=?, locked_until=?, updated_at=? "
                        + "where id=?",
                displayName, emailCiphertext, status, lockedUntil, now(), userId);
        if (!"LOCKED".equals(status)) {
            jdbc.update("update sec_users set failed_attempts=0 where id=?", userId);
        }
    }

    public void updateProfileFields(String userId, String displayName, String emailCiphertext) {
        jdbc.update("update sec_users set display_name=?, email_ciphertext=?, updated_at=? where id=?",
                displayName, emailCiphertext, now(), userId);
    }

    public void updatePasswordHash(String userId, String passwordHash) {
        jdbc.update("update sec_users set password_hash=?, auth_version=auth_version+1, updated_at=? where id=?",
                passwordHash, now(), userId);
    }

    public void replaceRoles(String tenantId, String userId, List<String> roleCodes) {
        jdbc.update("delete from sec_user_roles where tenant_id=? and user_id=?", tenantId, userId);
        roleCodes.forEach(code -> assignRole(tenantId, userId, code));
    }

    private static void appendFilters(StringBuilder sql, List<Object> args, String keyword, String status) {
        if (keyword != null && !keyword.isBlank()) {
            sql.append(" and (lower(username) like ? or lower(display_name) like ?)");
            String pattern = "%" + keyword.toLowerCase() + "%";
            args.add(pattern);
            args.add(pattern);
        }
        if (status != null && !status.isBlank()) {
            sql.append(" and status=?");
            args.add(status);
        }
    }

    public void saveRefreshToken(String hash, String id, String tenantId, String userId, String sessionId,
                                 OffsetDateTime expiresAt) {
        jdbc.update("insert into sec_refresh_tokens(token_hash,id,tenant_id,user_id,session_id,expires_at,created_at) "
                + "values(?,?,?,?,?,?,?)", hash, id, tenantId, userId, sessionId, expiresAt, now());
    }

    public Optional<RefreshTokenRecord> findRefreshToken(String hash) {
        return jdbc.query("select * from sec_refresh_tokens where token_hash=?", (rs, n) -> new RefreshTokenRecord(
                rs.getString("token_hash"), rs.getString("id"), rs.getString("tenant_id"), rs.getString("user_id"),
                rs.getString("session_id"), rs.getObject("expires_at", OffsetDateTime.class),
                rs.getObject("revoked_at", OffsetDateTime.class), rs.getString("replaced_by")), hash).stream().findFirst();
    }

    public boolean revokeRefreshToken(String hash, String replacementId) {
        return jdbc.update("update sec_refresh_tokens set revoked_at=?, replaced_by=? where token_hash=? and revoked_at is null",
                now(), replacementId, hash) == 1;
    }

    public void revokeSessionRefreshTokens(String sessionId) {
        jdbc.update("update sec_refresh_tokens set revoked_at=? where session_id=? and revoked_at is null", now(), sessionId);
    }

    public void revokeAccessToken(String jti, OffsetDateTime expiresAt) {
        jdbc.update("insert into sec_revoked_tokens(jti,expires_at) values(?,?)", jti, expiresAt);
    }

    public boolean isAccessTokenRevoked(String jti) {
        Integer count = jdbc.queryForObject("select count(*) from sec_revoked_tokens where jti=?", Integer.class, jti);
        return count != null && count > 0;
    }

    public List<RevokedTokenRecord> activeRevokedTokens() {
        return jdbc.query("select jti,expires_at from sec_revoked_tokens where expires_at>?",
                (rs, n) -> new RevokedTokenRecord(rs.getString("jti"),
                        rs.getObject("expires_at", OffsetDateTime.class)), now());
    }

    public static OffsetDateTime now() {
        return OffsetDateTime.now(ZoneOffset.UTC);
    }

    public record RefreshTokenRecord(String hash, String id, String tenantId, String userId, String sessionId,
                                     OffsetDateTime expiresAt, OffsetDateTime revokedAt, String replacedBy) {
    }

    public record RevokedTokenRecord(String jti, OffsetDateTime expiresAt) {
    }
}
