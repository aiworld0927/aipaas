package com.aipaas.platform.ingest;

import java.nio.file.Path;

/** 解析调用抽象：便于单元测试替换为本地桩实现。 */
public interface DocumentParseClient {

    ParseOutcome parse(ParseRequest request);

    record ParseRequest(String documentId, String tenantId, String filename, String format, String standardNo,
                        Path plaintext) {
    }

    record ParseOutcome(boolean success, String parser, String failureCode, String failureMessage,
                        boolean retryable, int version, int pages, int chunkCount, int warningCount,
                        String artifactKey, String resultJson) {
    }
}
