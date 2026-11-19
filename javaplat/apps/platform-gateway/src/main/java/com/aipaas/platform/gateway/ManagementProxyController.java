package com.aipaas.platform.gateway;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.observability.TracingService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** 管理 API 的流式反向代理；管理服务仍独立验签并执行业务授权。 */
@RestController
public class ManagementProxyController {

    private static final Set<String> HOP_BY_HOP = Set.of("connection", "keep-alive", "proxy-authenticate",
            "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length", "expect");
    private static final Set<String> EXTERNAL_IDENTITY = Set.of("x-user-id", "x-tenant-id", "x-roles", "x-permissions",
            "x-kb-ids", "x-security-level");

    private final HttpClient client;
    private final String managementUrl;
    private final Duration readTimeout;
    private final TracingService tracing;

    public ManagementProxyController(
            @Value("${aipaas.management.service-url:http://platform-management:8080}") String managementUrl,
            @Value("${aipaas.management.connect-timeout:PT5S}") Duration connectTimeout,
            @Value("${aipaas.management.read-timeout:PT120S}") Duration readTimeout,
            TracingService tracing) {
        this.managementUrl = managementUrl.replaceAll("/$", "");
        this.readTimeout = readTimeout;
        this.tracing = tracing;
        this.client = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(connectTimeout).build();
    }

    @RequestMapping("/api/v1/**")
    public ResponseEntity<StreamingResponseBody> proxy(HttpServletRequest request) {
        HttpResponse<InputStream> upstream;
        try {
            upstream = client.send(upstreamRequest(request), HttpResponse.BodyHandlers.ofInputStream());
        } catch (java.net.http.HttpTimeoutException ex) {
            throw new ApiException(ErrorCodes.SYS_TIMEOUT, HttpStatus.GATEWAY_TIMEOUT, "管理服务响应超时");
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new ApiException(ErrorCodes.SYS_DEPENDENCY_UNAVAILABLE, HttpStatus.SERVICE_UNAVAILABLE, "管理服务调用已中断");
        } catch (IOException ex) {
            throw new ApiException(ErrorCodes.SYS_DEPENDENCY_UNAVAILABLE, HttpStatus.SERVICE_UNAVAILABLE, "管理服务暂不可用");
        }

        HttpHeaders headers = new HttpHeaders();
        upstream.headers().map().forEach((name, values) -> {
            if (!HOP_BY_HOP.contains(name.toLowerCase(Locale.ROOT))) {
                headers.put(name, new ArrayList<>(values));
            }
        });
        headers.remove(HttpHeaders.CONTENT_LENGTH);
        headers.set(TraceContext.TRACE_HEADER, TraceContext.current());
        if (upstream.headers().firstValue(HttpHeaders.CONTENT_TYPE)
                .filter(value -> value.startsWith(MediaType.TEXT_EVENT_STREAM_VALUE)).isPresent()) {
            headers.set(HttpHeaders.CACHE_CONTROL, "no-cache");
            headers.set("X-Accel-Buffering", "no");
        }
        StreamingResponseBody body = output -> {
            try (InputStream input = upstream.body()) {
                input.transferTo(output);
                output.flush();
            }
        };
        return ResponseEntity.status(upstream.statusCode()).headers(headers).body(body);
    }

    private HttpRequest upstreamRequest(HttpServletRequest request) {
        String query = request.getQueryString();
        URI uri = URI.create(managementUrl + request.getRequestURI() + (query == null ? "" : "?" + query));
        HttpRequest.BodyPublisher body = hasBody(request)
                ? HttpRequest.BodyPublishers.ofInputStream(() -> {
                    try {
                        return request.getInputStream();
                    } catch (IOException ex) {
                        throw new IllegalStateException("Unable to read request body", ex);
                    }
                }) : HttpRequest.BodyPublishers.noBody();
        HttpRequest.Builder builder = HttpRequest.newBuilder(uri).timeout(readTimeout)
                .method(request.getMethod(), body);
        Enumeration<String> names = request.getHeaderNames();
        while (names != null && names.hasMoreElements()) {
            String name = names.nextElement();
            String normalized = name.toLowerCase(Locale.ROOT);
            if (HOP_BY_HOP.contains(normalized) || EXTERNAL_IDENTITY.contains(normalized)
                    || normalized.equals(TraceContext.TRACE_HEADER.toLowerCase(Locale.ROOT))
                    || normalized.equals(TraceContext.REQUEST_ID_HEADER.toLowerCase(Locale.ROOT))
                    || normalized.equals(TraceContext.TRACEPARENT_HEADER.toLowerCase(Locale.ROOT))) continue;
            Enumeration<String> values = request.getHeaders(name);
            while (values.hasMoreElements()) builder.header(name, values.nextElement());
        }
        String traceId = TraceContext.current();
        builder.header(TraceContext.REQUEST_ID_HEADER, traceId);
        builder.header(TraceContext.TRACE_HEADER, traceId);
        String traceparent = tracing.currentTraceparent();
        if (traceparent != null) builder.header(TraceContext.TRACEPARENT_HEADER, traceparent);
        SecurityPrincipal principal = SecurityPrincipal.current();
        if (!"anonymous".equals(principal.userId())) {
            builder.header("X-User-Id", principal.userId());
            builder.header("X-Tenant-Id", principal.tenantId());
            builder.header("X-Roles", String.join(",", principal.roles()));
            builder.header("X-Permissions", String.join(",", principal.permissions()));
            Object kbIds = request.getAttribute(GatewayJwtAuthenticationFilter.KB_IDS_ATTRIBUTE);
            if (kbIds instanceof List<?> list) {
                builder.header("X-Kb-Ids", String.join(",", list.stream().map(String::valueOf).toList()));
            }
        }
        return builder.build();
    }

    private static boolean hasBody(HttpServletRequest request) {
        return request.getContentLengthLong() > 0 || request.getHeader(HttpHeaders.TRANSFER_ENCODING) != null
                || !Set.of("GET", "HEAD", "OPTIONS").contains(request.getMethod());
    }
}
