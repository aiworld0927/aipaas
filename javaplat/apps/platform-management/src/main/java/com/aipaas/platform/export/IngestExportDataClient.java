package com.aipaas.platform.export;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.io.IOException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 通过内部 HTTP 读取 ingest-worker 的条款与简介；分页拉全，避免条款遗漏。 */
@Service
public class IngestExportDataClient implements ExportDataClient {

    private static final int PAGE_SIZE = 500;

    private final RestClient restClient;
    private final ObjectMapper mapper;

    public IngestExportDataClient(ObjectMapper mapper,
                                  @Value("${aipaas.parse.service-url:http://ingest-worker:8000}") String serviceUrl,
                                  @Value("${aipaas.parse.connect-timeout:PT5S}") Duration connectTimeout,
                                  @Value("${aipaas.parse.read-timeout:PT60S}") Duration readTimeout) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout((int) connectTimeout.toMillis());
        factory.setReadTimeout((int) readTimeout.toMillis());
        this.restClient = RestClient.builder().baseUrl(serviceUrl).requestFactory(factory).build();
        this.mapper = mapper;
    }

    @Override
    public ClauseData clauses(String documentId, String tenantId) {
        List<ClauseItem> items = new ArrayList<>();
        int version = 0;
        int expected = -1;
        int page = 1;
        while (true) {
            Map<String, Object> params = new LinkedHashMap<>();
            params.put("page", page);
            params.put("pageSize", PAGE_SIZE);
            if (tenantId != null && !tenantId.isBlank()) {
                params.put("tenant_id", tenantId);
            }
            JsonNode data = get("/api/v1/documents/{documentId}/clauses", documentId, params);
            version = data.path("version").asInt(version);
            if (expected < 0) {
                expected = data.path("clauseCount").asInt(data.path("total").asInt(0));
            }
            for (JsonNode node : data.path("items")) {
                items.add(new ClauseItem(
                        text(node, "clauseNo"), text(node, "title"), text(node, "text"),
                        text(node, "chapterPath"), integer(node, "pageNo"), integer(node, "level"),
                        text(node, "chunkId")));
            }
            int total = data.path("total").asInt(items.size());
            if (items.size() >= total || data.path("items").isEmpty() || page > 10_000) {
                break;
            }
            page++;
        }
        if (expected >= 0 && items.size() != expected) {
            throw new ExportDataException("BIZ.EXPORT_NOT_READY",
                    "条款读取不完整：期望 " + expected + " 条，实际 " + items.size() + " 条");
        }
        return new ClauseData(documentId, version, items.size(), List.copyOf(items));
    }

    @Override
    public SummaryData summary(String documentId, String tenantId) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("regenerate", false);
        JsonNode data;
        try {
            String raw = restClient.post()
                    .uri("/api/v1/documents/{documentId}/summaries", documentId)
                    .contentType(MediaType.APPLICATION_JSON)
                    .header("X-Tenant-Id", tenantId == null ? "" : tenantId)
                    .body(body)
                    .retrieve()
                    .body(String.class);
            data = unwrap(raw, documentId);
        } catch (RestClientException ex) {
            throw new ExportDataException("BIZ.EXPORT_NOT_READY", "标准简介生成失败: " + ex.getMessage(), ex);
        }
        List<SummaryElement> elements = new ArrayList<>();
        for (JsonNode node : data.path("elements")) {
            elements.add(new SummaryElement(text(node, "key"), text(node, "label"), text(node, "value"),
                    text(node, "sourceClauseNo"), text(node, "sourceQuote")));
        }
        return new SummaryData(documentId, text(data, "standardNo"), text(data, "standardName"),
                text(data, "scope"), text(data, "mainContent"), text(data, "effectiveAt"), List.copyOf(elements),
                text(data, "generatedAt"), text(data, "model"), text(data, "promptTemplateVersion"),
                data.path("confidence").asDouble(0), data.path("documentVersion").asInt(0));
    }

    private JsonNode get(String uri, String documentId, Map<String, Object> params) {
        try {
            String raw = restClient.get()
                    .uri(builder -> {
                        builder.path(uri);
                        params.forEach(builder::queryParam);
                        return builder.build(documentId);
                    })
                    .retrieve()
                    .body(String.class);
            return unwrap(raw, documentId);
        } catch (RestClientException ex) {
            throw new ExportDataException("BIZ.EXPORT_NOT_READY", "条款数据不可用: " + ex.getMessage(), ex);
        }
    }

    private JsonNode unwrap(String raw, String documentId) {
        if (raw == null || raw.isBlank()) {
            throw new ExportDataException("BIZ.EXPORT_NOT_READY", "解析服务返回空响应: " + documentId);
        }
        try {
            JsonNode root = mapper.readTree(raw);
            if (root.path("code").asInt(-1) != 200 || root.path("data").isMissingNode()) {
                throw new ExportDataException("BIZ.EXPORT_NOT_READY",
                        root.path("message").asText("解析产物不可用"));
            }
            return root.path("data");
        } catch (IOException ex) {
            throw new ExportDataException("BIZ.EXPORT_NOT_READY", "解析服务响应非法 JSON", ex);
        }
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.path(field);
        return value.isMissingNode() || value.isNull() ? null : value.asText();
    }

    private static Integer integer(JsonNode node, String field) {
        JsonNode value = node.path(field);
        return value.isMissingNode() || value.isNull() ? null : value.asInt();
    }
}
