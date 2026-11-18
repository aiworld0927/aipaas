package com.aipaas.platform.common.api;

import org.springframework.http.HttpStatus;

/** 业务异常：携带统一错误码与 HTTP 状态。 */
public class ApiException extends RuntimeException {

    private final String errorCode;
    private final HttpStatus status;
    private final String reason;

    public ApiException(String errorCode, HttpStatus status, String reason) {
        super(reason);
        this.errorCode = errorCode;
        this.status = status;
        this.reason = reason;
    }

    public static ApiException notImplemented() {
        return new ApiException(ErrorCodes.SYS_NOT_IMPLEMENTED, HttpStatus.NOT_IMPLEMENTED,
                "P0 契约已定义，骨架服务尚未实现该接口");
    }

    public static ApiException notFound(String reason) {
        return new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.NOT_FOUND, reason);
    }

    public String getErrorCode() {
        return errorCode;
    }

    public HttpStatus getStatus() {
        return status;
    }

    public String getReason() {
        return reason;
    }
}
