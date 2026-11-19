package com.aipaas.platform.document;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/** P0 索引由 RAG 服务管理，尚未接入；记录日志保证删除链路可观测。 */
@Component
public class LoggingVectorIndexGateway implements VectorIndexGateway {

    private static final Logger log = LoggerFactory.getLogger(LoggingVectorIndexGateway.class);

    @Override
    public void deleteDocument(String tenantId, String kbId, String documentId) {
        log.info("vector index delete accepted kbId={} documentId={}", kbId, documentId);
    }
}
