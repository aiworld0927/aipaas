package com.aipaas.platform.common.web;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.UUID;

/** 注入 traceId：优先复用 W3C traceparent / 上游 X-Request-Id，否则生成；始终回写 X-Trace-Id。 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class TraceIdFilter extends OncePerRequestFilter {

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String[] traceparent = TraceContext.parseTraceparent(request.getHeader(TraceContext.TRACEPARENT_HEADER));
        String incoming = request.getHeader(TraceContext.TRACE_HEADER);
        if (!StringUtils.hasText(incoming)) {
            incoming = request.getHeader(TraceContext.REQUEST_ID_HEADER);
        }
        String traceId;
        if (traceparent != null) {
            traceId = traceparent[0];
        } else if (StringUtils.hasText(incoming)) {
            traceId = incoming;
        } else {
            traceId = "tr_" + UUID.randomUUID().toString().replace("-", "");
        }
        request.setAttribute(TraceContext.REQUEST_ATTR, traceId);
        response.setHeader(TraceContext.TRACE_HEADER, traceId);
        MDC.put(TraceContext.MDC_KEY, traceId);
        try {
            chain.doFilter(request, response);
        } finally {
            MDC.remove(TraceContext.MDC_KEY);
            MDC.remove(TraceContext.MDC_SPAN_KEY);
        }
    }
}
