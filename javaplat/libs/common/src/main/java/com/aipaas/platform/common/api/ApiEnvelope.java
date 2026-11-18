package com.aipaas.platform.common.api;

/**
 * 统一响应信封：{@code {code,message,data,traceId}}。
 * 成功 code 固定为 {@link ErrorCodes#OK}；失败使用 AUTH/PERM/PARAM/BIZ/SYS/RATE 分段错误码。
 */
public record ApiEnvelope<T>(String code, String message, T data, String traceId) {

    public static <T> ApiEnvelope<T> ok(T data, String traceId) {
        return new ApiEnvelope<>(ErrorCodes.OK, "success", data, traceId);
    }

    public static ApiEnvelope<ErrorInfo> error(String code, String message, ErrorInfo data, String traceId) {
        return new ApiEnvelope<>(code, message, data, traceId);
    }

    public static ApiEnvelope<ErrorInfo> error(String code, String message, String reason, String traceId) {
        return error(code, message, ErrorInfo.of(reason), traceId);
    }
}
