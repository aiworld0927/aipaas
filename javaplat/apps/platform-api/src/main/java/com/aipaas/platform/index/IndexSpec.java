package com.aipaas.platform.index;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import org.springframework.http.HttpStatus;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * #49 索引创建参数。
 *
 * <p>P0 固定 {@code HNSW / COSINE / 自动槽位}；{@code indexType/metric/params/indexSlot/alias/version/env}
 * 为 P2 预留字段，创建时原样保存并回显。集合名与别名同时编码模型与索引类型，为 P1 多模型（#45）
 * 与多索引类型（#50 倒排 / #51 生命周期）预留命名契约。</p>
 */
public record IndexSpec(String kbId, String indexType, String metric, Map<String, Object> params,
                        String indexSlot, String alias, String model, String version, String env) {

    public static final String HNSW = "HNSW";
    private static final Set<String> METRICS = Set.of("COSINE", "IP", "L2");
    private static final Set<String> SLOTS = Set.of("online", "offline");
    private static final Set<String> INDEX_TYPES = Set.of(HNSW);

    public static IndexSpec of(String kbId, String indexType, String metric, Map<String, Object> params,
                               String indexSlot, String alias, String model, String version, String env) {
        String resolvedKb = kbId == null ? "" : kbId.trim();
        if (resolvedKb.isEmpty()) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "kbId 不能为空");
        }
        String type = indexType == null || indexType.isBlank() ? HNSW : indexType.trim().toUpperCase(Locale.ROOT);
        if (!INDEX_TYPES.contains(type)) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST,
                    "P0 仅支持 HNSW 索引，收到：" + type + "（#50/#51 预留）");
        }
        String resolvedMetric = metric == null || metric.isBlank()
                ? "COSINE" : metric.trim().toUpperCase(Locale.ROOT);
        if (!METRICS.contains(resolvedMetric)) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "metric 仅支持 " + METRICS);
        }
        Map<String, Object> merged = new LinkedHashMap<>();
        merged.put("M", 16);
        merged.put("efConstruction", 200);
        if (params != null) {
            merged.putAll(params);
        }
        if (!(merged.get("M") instanceof Number) || ((Number) merged.get("M")).intValue() < 1
                || !(merged.get("efConstruction") instanceof Number)
                || ((Number) merged.get("efConstruction")).intValue() < 1) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST,
                    "HNSW 参数 M/efConstruction 必须为正整数");
        }
        String slot = indexSlot == null || indexSlot.isBlank()
                ? "auto" : indexSlot.trim().toLowerCase(Locale.ROOT);
        if (!"auto".equals(slot) && !SLOTS.contains(slot)) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST,
                    "indexSlot 仅支持 auto/online/offline");
        }
        String resolvedModel = model == null || model.isBlank() ? "bge-m3" : model.trim();
        String resolvedAlias = alias == null || alias.isBlank()
                ? "kb_" + safe(resolvedKb) + "__" + modelToken(resolvedModel) + "__" + type.toLowerCase(Locale.ROOT)
                : safe(alias);
        return new IndexSpec(resolvedKb, type, resolvedMetric, Map.copyOf(merged), slot, resolvedAlias,
                resolvedModel, version, env);
    }

    /** 自动槽位：无活跃索引用 online，有则用 offline，配合双集合热切换。 */
    public String resolveSlot(boolean hasActive) {
        if (!"auto".equals(indexSlot)) {
            return indexSlot;
        }
        return hasActive ? "offline" : "online";
    }

    public String collectionName(String slot, int versionNum) {
        return alias + "__" + slot + "__v" + versionNum;
    }

    public Map<String, Object> toView() {
        Map<String, Object> view = new LinkedHashMap<>();
        view.put("indexType", indexType);
        view.put("metric", metric);
        view.put("params", params);
        view.put("indexSlot", indexSlot);
        view.put("alias", alias);
        view.put("model", model);
        view.put("version", version);
        view.put("env", env);
        return view;
    }

    static String modelToken(String model) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(model.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest).substring(0, 8);
        } catch (Exception ex) {
            throw new IllegalStateException(ex);
        }
    }

    private static String safe(String value) {
        String cleaned = value == null ? "" : value.replaceAll("[^A-Za-z0-9._-]", "_");
        return cleaned.substring(0, Math.min(cleaned.length(), 64));
    }
}
