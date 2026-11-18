package com.aipaas.platform.common.web;

import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.api.ErrorInfo;
import com.aipaas.platform.common.api.ValidationIssue;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.multipart.support.MissingServletRequestPartException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.NoHandlerFoundException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

import java.util.List;

/** 统一异常到信封的映射；未实现的契约接口返回 501 + SYS.NOT_IMPLEMENTED。 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(ApiException.class)
    public ResponseEntity<ApiEnvelope<ErrorInfo>> handleApi(ApiException ex) {
        return ResponseEntity.status(ex.getStatus())
                .body(ApiEnvelope.error(ex.getErrorCode(), ex.getReason(), ex.getReason(), TraceContext.current()));
    }

    @ExceptionHandler({NoHandlerFoundException.class, NoResourceFoundException.class})
    public ResponseEntity<ApiEnvelope<ErrorInfo>> handleNotImplemented(Exception ex) {
        return ResponseEntity.status(HttpStatus.NOT_IMPLEMENTED)
                .body(ApiEnvelope.error(ErrorCodes.SYS_NOT_IMPLEMENTED,
                        "P0 契约已定义，骨架服务尚未实现该接口", ErrorInfo.of("not implemented"),
                        TraceContext.current()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiEnvelope<ErrorInfo>> handleValidation(MethodArgumentNotValidException ex) {
        List<ValidationIssue> details = ex.getBindingResult().getFieldErrors().stream()
                .map(e -> new ValidationIssue(e.getField(), ErrorCodes.PARAM_INVALID,
                        e.getDefaultMessage() == null ? "invalid" : e.getDefaultMessage()))
                .toList();
        return ResponseEntity.badRequest().body(ApiEnvelope.error(ErrorCodes.PARAM_INVALID,
                "请求参数不合法", ErrorInfo.of("validation failed", details), TraceContext.current()));
    }

    @ExceptionHandler({MissingRequestHeaderException.class, MissingServletRequestParameterException.class})
    public ResponseEntity<ApiEnvelope<ErrorInfo>> handleMissing(Exception ex) {
        String field = ex instanceof MissingRequestHeaderException h ? h.getHeaderName()
                : ((MissingServletRequestParameterException) ex).getParameterName();
        return ResponseEntity.badRequest().body(ApiEnvelope.error(ErrorCodes.PARAM_MISSING,
                "缺少必填参数", ErrorInfo.of("missing: " + field), TraceContext.current()));
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiEnvelope<ErrorInfo>> handleUnreadable(HttpMessageNotReadableException ex) {
        return ResponseEntity.badRequest().body(ApiEnvelope.error(ErrorCodes.PARAM_INVALID,
                "请求体不是合法 JSON", ErrorInfo.of("malformed json"), TraceContext.current()));
    }

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    public ResponseEntity<ApiEnvelope<ErrorInfo>> handleUploadSize(MaxUploadSizeExceededException ex) {
        return ResponseEntity.status(HttpStatus.PAYLOAD_TOO_LARGE).body(ApiEnvelope.error(ErrorCodes.PARAM_FILE_TOO_LARGE,
                "单文件不得超过 200MB", ErrorInfo.of("upload size limit exceeded"), TraceContext.current()));
    }

    @ExceptionHandler({MethodArgumentTypeMismatchException.class, MissingServletRequestPartException.class})
    public ResponseEntity<ApiEnvelope<ErrorInfo>> handleParameterType(Exception ex) {
        return ResponseEntity.badRequest().body(ApiEnvelope.error(ErrorCodes.PARAM_INVALID,
                "请求参数或 multipart 文件不合法", ErrorInfo.of("invalid request parameter"), TraceContext.current()));
    }

    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    public ResponseEntity<ApiEnvelope<ErrorInfo>> handleMethod(HttpRequestMethodNotSupportedException ex) {
        return ResponseEntity.status(HttpStatus.METHOD_NOT_ALLOWED).body(ApiEnvelope.error(
                ErrorCodes.PARAM_INVALID, "HTTP 方法不支持", ErrorInfo.of(ex.getMessage()), TraceContext.current()));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiEnvelope<ErrorInfo>> handleUnknown(Exception ex) {
        log.error("unhandled exception, traceId={}", TraceContext.current(), ex);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(ApiEnvelope.error(ErrorCodes.SYS_INTERNAL, "服务内部错误",
                        ErrorInfo.of("internal error: " + ex.getClass().getSimpleName()), TraceContext.current()));
    }
}
