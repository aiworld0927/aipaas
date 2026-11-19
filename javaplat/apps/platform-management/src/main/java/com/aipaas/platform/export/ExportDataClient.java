package com.aipaas.platform.export;

import java.util.List;

/** 从 ingest-worker 拉取条目化条款与标准简介（内部调用，已由 Java 完成权限校验）。 */
public interface ExportDataClient {

    ClauseData clauses(String documentId, String tenantId);

    SummaryData summary(String documentId, String tenantId);

    record ClauseItem(String clauseNo, String title, String text, String chapterPath,
                      Integer pageNo, Integer level, String chunkId) {
    }

    record ClauseData(String documentId, int version, int clauseCount, List<ClauseItem> items) {
    }

    record SummaryElement(String key, String label, String value, String sourceClauseNo, String sourceQuote) {
    }

    record SummaryData(String documentId, String standardNo, String standardName, String scope, String mainContent,
                       String effectiveAt, List<SummaryElement> elements, String generatedAt, String model,
                       String promptTemplateVersion, double confidence, int documentVersion) {
    }

    class ExportDataException extends RuntimeException {
        private final String code;

        public ExportDataException(String code, String message, Throwable cause) {
            super(message, cause);
            this.code = code;
        }

        public ExportDataException(String code, String message) {
            this(code, message, null);
        }

        public String code() {
            return code;
        }
    }
}
