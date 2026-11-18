package com.aipaas.platform.ingest;

import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.UUID;

/** 解析产物版本登记：对象存储保存正文，本表记录版本、产物键与摘要。 */
@Repository
public class ParseResultRepository {

    private final JdbcTemplate jdbc;

    public ParseResultRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public int save(String tenantId, String documentId, String taskId, String parser, boolean success,
                    String failureCode, int pages, int chunkCount, String artifactKey, String resultJson,
                    OffsetDateTime parsedAt) {
        for (int attempt = 0; attempt < 3; attempt++) {
            int version = nextVersion(documentId);
            try {
                jdbc.update("insert into document_parse_results(id,tenant_id,document_id,task_id,version,parser,"
                                + "success,failure_code,pages,chunk_count,artifact_key,result_json,parsed_at,created_at) "
                                + "values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        "prs_" + UUID.randomUUID().toString().replace("-", ""), tenantId, documentId, taskId,
                        version, parser, success, failureCode, pages, chunkCount, artifactKey, resultJson,
                        parsedAt, OffsetDateTime.now());
                return version;
            } catch (DuplicateKeyException ex) {
                // 并发解析同一文档时版本号冲突，重新取号；仍失败则向上抛出，不静默吞异常。
                if (attempt == 2) {
                    throw ex;
                }
            }
        }
        throw new IllegalStateException("parse result version allocation failed");
    }

    public int nextVersion(String documentId) {
        Integer value = jdbc.queryForObject("select coalesce(max(version),0)+1 from document_parse_results "
                + "where document_id=?", Integer.class, documentId);
        return value == null ? 1 : value;
    }
}
