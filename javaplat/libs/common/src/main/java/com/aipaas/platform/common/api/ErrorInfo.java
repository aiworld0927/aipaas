package com.aipaas.platform.common.api;

import java.util.List;

/** 失败信封中的 data 部分。 */
public record ErrorInfo(String reason, List<ValidationIssue> details, boolean retryable) {

    public static ErrorInfo of(String reason) {
        return new ErrorInfo(reason, null, false);
    }

    public static ErrorInfo of(String reason, List<ValidationIssue> details) {
        return new ErrorInfo(reason, details, false);
    }
}
