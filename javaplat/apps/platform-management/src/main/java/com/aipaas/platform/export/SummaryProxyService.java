package com.aipaas.platform.export;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;

/** 标准简介网关：Java 侧完成文档归属与 KB 权限校验后，代理 app-ingest-worker 的 #405 接口。 */
@Service
public class SummaryProxyService {

    private final RestClient restClient;
    private final ObjectMapper mapper;

    public SummaryProxyService(ObjectMapper mapper,
                               @Value("${aipaas.parse.service-url:http://app-ingest-worker:8000}") String serviceUrl,
                               @Value("${aipaas.parse.connect-timeout:PT5S}") Duration connectTimeout,
                               @Value("${aipaas.parse.read-timeout:PT60S}") Duration readTimeout) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout((int) connectTimeout.toMillis());
        factory.setReadTimeout((int) readTimeout.toMillis());
        this.restClient = RestClient.builder().baseUrl(serviceUrl).requestFactory(factory).build();
        this.mapper = mapper;
    }

    public Map<String, Object> latest(String documentId, String tenantId) {
        try {
            String raw = restClient.get()
                    .uri("/api/v1/documents/{documentId}/summaries/latest", documentId)
                    .header("X-Tenant-Id", tenantId == null ? "" : tenantId)
                    .retrieve()
                    .body(String.class);
            return payload(raw);
        } catch (RestClientResponseException ex) {
            throw translate(ex);
        } catch (RestClientException ex) {
            throw unavailable(ex);
        }
    }

    public Map<String, Object> generate(String documentId, String tenantId, boolean regenerate) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("regenerate", regenerate);
        try {
            String raw = restClient.post()
                    .uri("/api/v1/documents/{documentId}/summaries", documentId)
                    .contentType(MediaType.APPLICATION_JSON)
                    .header("X-Tenant-Id", tenantId == null ? "" : tenantId)
                    .body(body)
                    .retrieve()
                    .body(String.class);
            return payload(raw);
        } catch (RestClientResponseException ex) {
            throw translate(ex);
        } catch (RestClientException ex) {
            throw unavailable(ex);
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> payload(String raw) {
        try {
            JsonNode root = mapper.readTree(raw == null ? "{}" : raw);
            if (root.path("code").asInt(-1) != 200) {
                throw new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.NOT_FOUND,
                        root.path("message").asText("标准简介不存在"));
            }
            return mapper.convertValue(root.path("data"), Map.class);
        } catch (ApiException ex) {
            throw ex;
        } catch (Exception ex) {
            throw unavailable(ex);
        }
    }

    private ApiException translate(RestClientResponseException ex) {
        if (ex.getStatusCode().value() == 404) {
            return new ApiException(ErrorCodes.BIZ_NOT_FOUND, HttpStatus.NOT_FOUND, "标准简介或文档不存在");
        }
        if (ex.getStatusCode().value() == 403) {
            return new ApiException(ErrorCodes.PERM_DOC_FORBIDDEN, HttpStatus.FORBIDDEN, "无权访问该文档");
        }
        return unavailable(ex);
    }

    private ApiException unavailable(Exception ex) {
        return new ApiException(ErrorCodes.SYS_DEPENDENCY_UNAVAILABLE, HttpStatus.SERVICE_UNAVAILABLE,
                "解析服务暂不可用: " + ex.getMessage());
    }
}
