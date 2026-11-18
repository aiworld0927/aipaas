package com.aipaas.platform.common.api;

/**
 * P0 错误码目录。契约约束：{@code ^(AUTH|PERM|PARAM|BIZ|SYS|RATE)\.[A-Z0-9_]+$}。
 * 新增错误码只允许在既有分段内追加，不得改变既有语义。
 */
public final class ErrorCodes {

    private ErrorCodes() {
    }

    public static final String OK = "OK";

    // AUTH -> 401
    public static final String AUTH_UNAUTHENTICATED = "AUTH.UNAUTHENTICATED";
    public static final String AUTH_TOKEN_EXPIRED = "AUTH.TOKEN_EXPIRED";
    public static final String AUTH_INVALID_CREDENTIALS = "AUTH.INVALID_CREDENTIALS";
    public static final String AUTH_ACCOUNT_LOCKED = "AUTH.ACCOUNT_LOCKED";

    // PERM -> 403
    public static final String PERM_FORBIDDEN = "PERM.FORBIDDEN";
    public static final String PERM_DOC_FORBIDDEN = "PERM.DOC_FORBIDDEN";
    public static final String PERM_UPLOAD_FORBIDDEN = "PERM.UPLOAD_FORBIDDEN";
    public static final String PERM_EXPORT_FORBIDDEN = "PERM.EXPORT_FORBIDDEN";

    // PARAM -> 400
    public static final String PARAM_INVALID = "PARAM.INVALID";
    public static final String PARAM_MISSING = "PARAM.MISSING";
    public static final String PARAM_QUERY_EMPTY = "PARAM.QUERY_EMPTY";
    public static final String PARAM_QUERY_TOO_LONG = "PARAM.QUERY_TOO_LONG";
    public static final String PARAM_UNSUPPORTED_FORMAT = "PARAM.UNSUPPORTED_FORMAT";
    public static final String PARAM_FILE_TOO_LARGE = "PARAM.FILE_TOO_LARGE";
    public static final String PARAM_IDEMPOTENCY_KEY_MISSING = "PARAM.IDEMPOTENCY_KEY_MISSING";

    // BIZ -> 404/409/422
    public static final String BIZ_NOT_FOUND = "BIZ.NOT_FOUND";
    public static final String BIZ_CONFLICT = "BIZ.CONFLICT";
    public static final String BIZ_DUPLICATE = "BIZ.DUPLICATE";
    public static final String BIZ_NO_ANSWER = "BIZ.NO_ANSWER";
    public static final String BIZ_CONTENT_BLOCKED = "BIZ.CONTENT_BLOCKED";
    public static final String BIZ_PROMPT_INJECTION = "BIZ.PROMPT_INJECTION";
    public static final String BIZ_TASK_NOT_RETRYABLE = "BIZ.TASK_NOT_RETRYABLE";
    public static final String BIZ_INDEX_NOT_READY = "BIZ.INDEX_NOT_READY";
    public static final String BIZ_EXPORT_NOT_READY = "BIZ.EXPORT_NOT_READY";

    // SYS -> 500/501/503
    public static final String SYS_INTERNAL = "SYS.INTERNAL";
    public static final String SYS_NOT_IMPLEMENTED = "SYS.NOT_IMPLEMENTED";
    public static final String SYS_DEPENDENCY_UNAVAILABLE = "SYS.DEPENDENCY_UNAVAILABLE";
    public static final String SYS_TIMEOUT = "SYS.TIMEOUT";
    public static final String SYS_DEGRADED = "SYS.DEGRADED";

    // RATE -> 429
    public static final String RATE_LIMITED = "RATE.LIMITED";
    public static final String RATE_QUOTA_EXCEEDED = "RATE.QUOTA_EXCEEDED";
}
