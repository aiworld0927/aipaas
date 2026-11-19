package com.aipaas.platform.ingest;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.io.IOException;
import java.nio.file.Path;
import java.time.Duration;

/** 通过 HTTP multipart 调用 Python 解析服务，明文只存在于临时文件与请求体。 */
@Service
public class HttpDocumentParseClient implements DocumentParseClient {

    private static final Logger log = LoggerFactory.getLogger(HttpDocumentParseClient.class);

    private final RestClient restClient;
    private final ObjectMapper mapper;
    private final String revisionMode;
    private final String commentMode;
    private final String formulaMode;

    public HttpDocumentParseClient(ObjectMapper mapper,
                                   @Value("${aipaas.parse.service-url:http://ingest-worker:8000}") String serviceUrl,
                                   @Value("${aipaas.parse.connect-timeout:PT5S}") Duration connectTimeout,
                                   @Value("${aipaas.parse.read-timeout:PT180S}") Duration readTimeout,
                                   @Value("${aipaas.parse.revision-mode:ignore}") String revisionMode,
                                   @Value("${aipaas.parse.comment-mode:ignore}") String commentMode,
                                   @Value("${aipaas.parse.formula-mode:result}") String formulaMode) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout((int) connectTimeout.toMillis());
        factory.setReadTimeout((int) readTimeout.toMillis());
        this.restClient = RestClient.builder().baseUrl(serviceUrl).requestFactory(factory).build();
        this.mapper = mapper;
        this.revisionMode = revisionMode;
        this.commentMode = commentMode;
        this.formulaMode = formulaMode;
    }

    @Override
    public ParseOutcome parse(ParseRequest request) {
        MultiValueMap<String, Object> parts = new LinkedMultiValueMap<>();
        parts.add("file", new NamedFileResource(request.plaintext(), request.filename()));
        parts.add("tenantId", request.tenantId());
        parts.add("format", request.format().toLowerCase());
        parts.add("revisionMode", revisionMode);
        parts.add("commentMode", commentMode);
        parts.add("formulaMode", formulaMode);
        if (request.standardNo() != null && !request.standardNo().isBlank()) {
            parts.add("standardNo", request.standardNo());
        }
        String body;
        try {
            body = restClient.post()
                    .uri("/api/v1/documents/{documentId}:parse", request.documentId())
                    .contentType(MediaType.MULTIPART_FORM_DATA)
                    .body(parts)
                    .retrieve()
                    .body(String.class);
        } catch (RestClientException ex) {
            throw new ParseClientException("解析服务调用失败: " + ex.getMessage(), ex);
        }
        return parseResponse(body, request);
    }

    private ParseOutcome parseResponse(String body, ParseRequest request) {
        if (body == null || body.isBlank()) {
            throw new ParseClientException("解析服务返回空响应");
        }
        JsonNode root;
        try {
            root = mapper.readTree(body);
        } catch (IOException ex) {
            throw new ParseClientException("解析服务响应不是合法 JSON", ex);
        }
        JsonNode data = root.path("data");
        if (data.isMissingNode() || data.isNull()) {
            throw new ParseClientException("解析服务响应缺少 data: " + root.path("message").asText(""));
        }
        JsonNode extras = data.path("extras");
        int version = data.path("version").asInt(0);
        String failureCode = textOrNull(data.path("failureCode"));
        String failureMessage = textOrNull(data.path("failureMessage"));
        boolean success = data.path("success").asBoolean(false);
        String artifactKey = "parse/" + request.tenantId() + "/" + request.documentId() + "/v" + version
                + "/parse-result.json";
        log.info("解析服务返回 document={} success={} parser={} failureCode={} version={}",
                request.documentId(), success, data.path("parser").asText(""), failureCode, version);
        return new ParseOutcome(success, data.path("parser").asText(""), failureCode, failureMessage,
                extras.path("failureRetryable").asBoolean(false), version, data.path("pages").asInt(0),
                extras.path("chunkCount").asInt(0), data.path("warnings").size(), artifactKey,
                data.toString());
    }

    private static String textOrNull(JsonNode node) {
        return node.isMissingNode() || node.isNull() ? null : node.asText();
    }

    /** 让 multipart 使用原始文件名，而不是临时文件名。 */
    private static final class NamedFileResource extends FileSystemResource {
        private final String filename;

        private NamedFileResource(Path path, String filename) {
            super(path);
            this.filename = filename == null || filename.isBlank() ? "upload.bin" : filename;
        }

        @Override
        public String getFilename() {
            return filename;
        }
    }
}
