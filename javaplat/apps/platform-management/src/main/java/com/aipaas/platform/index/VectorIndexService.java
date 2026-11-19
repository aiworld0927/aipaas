package com.aipaas.platform.index;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Executor;

/**
 * #49 HNSW 索引构建编排：
 * <ul>
 *   <li>构建成功后才切换别名（双集合热切换），构建失败旧索引继续服务；</li>
 *   <li>失败可重试，成功构建可回退到 previousCollection；</li>
 *   <li>每次构建持久化耗时、资源占用、chunk 数，支持全量重建。</li>
 * </ul>
 */
@Service
public class VectorIndexService {

    private static final Logger log = LoggerFactory.getLogger(VectorIndexService.class);
    private static final String QUEUED = "QUEUED";
    private static final String RUNNING = "RUNNING";
    private static final String SUCCEEDED = "SUCCEEDED";
    private static final String FAILED = "FAILED";
    private static final String BUILDING = "BUILDING";
    private static final String READY = "READY";
    private static final String EMPTY = "EMPTY";

    private final VectorIndexRepository repository;
    private final VectorIndexBackend backend;
    private final Executor executor;
    private final int dimension;
    private final ObjectMapper json;

    public VectorIndexService(VectorIndexRepository repository,
                              VectorIndexBackend backend,
                              @Qualifier("ingestExecutor") Executor executor,
                              @Value("${aipaas.index.dimension:1024}") int dimension,
                              ObjectMapper json) {
        this.repository = repository;
        this.backend = backend;
        this.executor = executor;
        this.dimension = dimension;
        this.json = json;
    }

    public record IndexBuildRequest(String kbId, Boolean fullRebuild, String indexType, String metric,
                                    Map<String, Object> params, String indexSlot, String alias, String model,
                                    String version, String env, List<String> documentIds) {
    }

    /* ------------------------------------------------------------------ 写操作 */

    public Map<String, Object> create(String tenantId, String idempotencyKey, IndexBuildRequest request) {
        return repository.findByIdempotencyKey(tenantId, idempotencyKey)
                .map(this::view)
                .orElseGet(() -> doCreate(tenantId, idempotencyKey, request));
    }

    private Map<String, Object> doCreate(String tenantId, String idempotencyKey, IndexBuildRequest request) {
        if (request == null || request.kbId() == null || request.kbId().isBlank()) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "kbId 不能为空");
        }
        String kbId = request.kbId().trim();
        if (!repository.knowledgeBaseExists(tenantId, kbId)) {
            throw ApiException.notFound("知识库不存在");
        }
        IndexSpec spec = IndexSpec.of(kbId, request.indexType(), request.metric(), request.params(),
                request.indexSlot(), request.alias(), request.model(), request.version(), request.env());
        List<String> documentIds = Boolean.TRUE.equals(request.fullRebuild()) ? null : request.documentIds();
        int totalChunks = repository.chunkTotal(tenantId, kbId, documentIds);
        if (totalChunks <= 0) {
            throw new ApiException(ErrorCodes.BIZ_INDEX_NOT_READY, HttpStatus.CONFLICT,
                    "暂无可索引内容：请先完成文档解析与向量化");
        }
        var active = repository.latestSucceededForAlias(tenantId, spec.alias());
        String slot = spec.resolveSlot(active.isPresent());
        int versionNum = repository.nextVersion(tenantId, spec.alias());
        String collection = spec.collectionName(slot, versionNum);
        OffsetDateTime now = VectorIndexRepository.now();
        IndexBuildRow row = new IndexBuildRow(
                "idx_" + UUID.randomUUID().toString().replace("-", "").substring(0, 12),
                tenantId, kbId, idempotencyKey, QUEUED, 0, spec, slot, collection,
                active.map(IndexBuildRow::collection).orElse(null), versionNum, dimension, totalChunks, 0,
                null, null, write(documentIds), 1, false, null, null, null, now, now);
        repository.insert(row);
        repository.updateKnowledgeBaseStatus(tenantId, kbId, BUILDING, now);
        executor.execute(() -> execute(tenantId, row.id()));
        log.info("创建索引构建 build={} kb={} alias={} collection={} chunks={}",
                row.id(), kbId, spec.alias(), collection, totalChunks);
        return view(repository.find(tenantId, row.id()).orElse(row));
    }

    public Map<String, Object> retry(String tenantId, String buildId) {
        IndexBuildRow row = require(tenantId, buildId);
        if (!FAILED.equals(row.status())) {
            throw new ApiException(ErrorCodes.BIZ_TASK_NOT_RETRYABLE, HttpStatus.CONFLICT,
                    "仅 FAILED 构建可重试，当前：" + row.status());
        }
        repository.requeue(buildId, VectorIndexRepository.now());
        repository.updateKnowledgeBaseStatus(tenantId, row.kbId(), BUILDING, VectorIndexRepository.now());
        executor.execute(() -> execute(tenantId, buildId));
        return view(require(tenantId, buildId));
    }

    /** 回退旧索引：别名切回 previousCollection，当前构建标记 rolledBack。 */
    public Map<String, Object> rollback(String tenantId, String buildId) {
        IndexBuildRow row = require(tenantId, buildId);
        if (!SUCCEEDED.equals(row.status())) {
            throw new ApiException(ErrorCodes.BIZ_CONFLICT, HttpStatus.CONFLICT, "仅 SUCCEEDED 构建可回退");
        }
        if (row.previousCollection() == null || row.previousCollection().isBlank()) {
            throw new ApiException(ErrorCodes.BIZ_CONFLICT, HttpStatus.CONFLICT, "该构建没有可回退的旧索引");
        }
        backend.switchAlias(row.spec().alias(), row.previousCollection());
        repository.markRolledBack(buildId, VectorIndexRepository.now());
        repository.updateKnowledgeBaseStatus(tenantId, row.kbId(), READY, VectorIndexRepository.now());
        log.warn("索引回退 build={} alias={} -> {}", buildId, row.spec().alias(), row.previousCollection());
        return view(require(tenantId, buildId));
    }

    /* ------------------------------------------------------------------ 构建执行 */

    private void execute(String tenantId, String buildId) {
        IndexBuildRow row = repository.find(tenantId, buildId).orElse(null);
        if (row == null) {
            return;
        }
        OffsetDateTime startedAt = VectorIndexRepository.now();
        repository.markRunning(buildId, 10, startedAt);
        long started = System.currentTimeMillis();
        VectorIndexBackend.BuildResult result = null;
        try {
            result = backend.build(row.spec(), row.collection(), row.totalChunks(), row.dimension());
            // 双集合热切换：构建成功后才切别名，保证切换前旧索引持续可检索
            backend.switchAlias(row.spec().alias(), row.collection());
            long duration = System.currentTimeMillis() - started;
            int indexed = result == null ? row.totalChunks() : result.indexedChunks();
            String resources = resourceJson(result, duration);
            repository.markSucceeded(buildId, indexed, duration, resources, VectorIndexRepository.now());
            repository.updateKnowledgeBaseStatus(tenantId, row.kbId(), READY, VectorIndexRepository.now());
            log.info("索引构建完成 build={} collection={} indexed={} durationMs={}",
                    buildId, row.collection(), indexed, duration);
        } catch (RuntimeException ex) {
            long duration = System.currentTimeMillis() - started;
            repository.markFailed(buildId, duration, resourceJson(result, duration), ex.getMessage(),
                    VectorIndexRepository.now());
            repository.updateKnowledgeBaseStatus(tenantId, row.kbId(), FAILED, VectorIndexRepository.now());
            log.error("索引构建失败 build={} collection={}，旧索引保持可用", buildId, row.collection(), ex);
        }
    }

    /* ------------------------------------------------------------------ 查询 */

    public Map<String, Object> get(String tenantId, String buildId) {
        return view(require(tenantId, buildId));
    }

    public Map<String, Object> status(String tenantId, String kbId) {
        if (!repository.knowledgeBaseExists(tenantId, kbId)) {
            throw ApiException.notFound("知识库不存在");
        }
        var latest = repository.latestSucceeded(tenantId, kbId);
        String indexStatus = repository.knowledgeBaseIndexStatus(tenantId, kbId);
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("kbId", kbId);
        view.put("indexStatus", latest.isEmpty() && EMPTY.equals(indexStatus) ? EMPTY : indexStatus);
        view.put("collection", latest.map(IndexBuildRow::collection).orElse(""));
        view.put("dimension", latest.map(IndexBuildRow::dimension).orElse(0));
        view.put("docCount", repository.documentCount(tenantId, kbId));
        view.put("chunkCount", latest.isEmpty() ? repository.indexedChunkTotal(tenantId, kbId)
                : latest.get().indexedChunks());
        view.put("lastBuildId", latest.map(IndexBuildRow::id).orElse(null));
        view.put("lastBuiltAt", latest.map(IndexBuildRow::finishedAt).map(Object::toString).orElse(null));
        view.put("alias", latest.map(row -> row.spec().alias()).orElse(null));
        view.put("version", latest.map(row -> row.spec().version()).orElse(null));
        view.put("env", latest.map(row -> row.spec().env()).orElse(null));
        return view;
    }

    private IndexBuildRow require(String tenantId, String buildId) {
        return repository.find(tenantId, buildId)
                .orElseThrow(() -> ApiException.notFound("索引构建不存在"));
    }

    private Map<String, Object> view(IndexBuildRow row) {
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("id", row.id());
        view.put("tenantId", row.tenantId());
        view.put("kbId", row.kbId());
        view.put("status", row.status());
        view.put("totalChunks", row.totalChunks());
        view.put("indexedChunks", row.indexedChunks());
        view.put("progress", row.progress());
        view.put("durationMs", row.durationMs());
        view.putAll(row.spec().toView());
        view.put("collection", row.collection());
        view.put("previousCollection", row.previousCollection());
        view.put("resolvedSlot", row.resolvedSlot());
        view.put("attempt", row.attempt());
        view.put("rolledBack", row.rolledBack());
        view.put("error", row.errorMessage());
        view.put("resourceUsage", readMap(row.resourceJson()));
        view.put("createdAt", row.createdAt() == null ? null : row.createdAt().toString());
        view.put("updatedAt", row.updatedAt() == null ? null : row.updatedAt().toString());
        view.put("startedAt", row.startedAt() == null ? null : row.startedAt().toString());
        view.put("finishedAt", row.finishedAt() == null ? null : row.finishedAt().toString());
        return view;
    }

    private String resourceJson(VectorIndexBackend.BuildResult result, long durationMs) {
        Map<String, Object> resources = new LinkedHashMap<>();
        if (result != null && result.resources() != null) {
            resources.putAll(result.resources());
        }
        resources.put("durationMs", durationMs);
        return write(resources);
    }

    private String write(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception ex) {
            return null;
        }
    }

    private Map<String, Object> readMap(String value) {
        if (value == null || value.isBlank()) {
            return Map.of();
        }
        try {
            return json.readValue(value, new TypeReference<>() {
            });
        } catch (Exception ex) {
            return Map.of();
        }
    }
}
