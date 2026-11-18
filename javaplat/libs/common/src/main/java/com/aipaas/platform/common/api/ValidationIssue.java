package com.aipaas.platform.common.api;

/** 参数校验明细。 */
public record ValidationIssue(String field, String code, String message) {
}
