package com.aipaas.platform.document;

/** 向量索引删除端口；P0 无独立向量写入，P1 接入 Milvus 时替换实现。 */
public interface VectorIndexGateway {

    void deleteDocument(String tenantId, String kbId, String documentId);
}
