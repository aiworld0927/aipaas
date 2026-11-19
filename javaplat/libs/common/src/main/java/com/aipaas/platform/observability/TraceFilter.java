package com.aipaas.platform.observability;

import com.aipaas.platform.common.web.TraceContext;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/** 为每个 HTTP 请求建立根 Span（含 401/403/500），记录状态码与耗时。 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class TraceFilter extends OncePerRequestFilter {

    private final TracingService tracing;

    public TraceFilter(TracingService tracing) {
        this.tracing = tracing;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String path = request.getRequestURI();
        if (isInfrastructure(path)) {
            chain.doFilter(request, response);
            return;
        }
        try (TracingService.Span span = tracing.startRoot(request.getMethod() + " " + path)) {
            span.attribute("http.method", request.getMethod())
                    .attribute("http.route", path)
                    .attribute("http.scheme", request.getScheme());
            try {
                chain.doFilter(request, response);
            } catch (ServletException | IOException | RuntimeException ex) {
                span.markError(ex);
                throw ex;
            } finally {
                span.attribute("http.status_code", response.getStatus());
                if (response.getStatus() >= 500) {
                    span.markErrorStatus();
                }
            }
        }
    }

    private static boolean isInfrastructure(String path) {
        return path.startsWith("/actuator") || path.equals("/healthz") || path.equals("/readyz")
                || path.equals("/metrics") || path.startsWith("/error");
    }
}
