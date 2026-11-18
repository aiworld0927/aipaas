package com.aipaas.platform.rbac;

import com.aipaas.platform.audit.AuditService;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.config.SecurityAlertService;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/** 在 DispatcherServlet 解析 multipart 之前执行实时 KB 上传授权。 */
@Component
public class UploadAuthorizationFilter extends OncePerRequestFilter {

    private final RbacService rbac;
    private final AuditService audit;
    private final SecurityAlertService alerts;
    private final ObjectMapper objectMapper;

    public UploadAuthorizationFilter(RbacService rbac, AuditService audit, SecurityAlertService alerts,
                                     ObjectMapper objectMapper) {
        this.rbac = rbac;
        this.audit = audit;
        this.alerts = alerts;
        this.objectMapper = objectMapper;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String uri = request.getRequestURI();
        String method = request.getMethod();
        boolean upload = "POST".equals(method) && uri.matches(".*/api/v1/knowledge-bases/[^/]+/documents$");
        boolean chunked = ("POST".equals(method)
                && uri.matches(".*/api/v1/knowledge-bases/[^/]+/documents/uploads:(init|complete)$"))
                || ("PUT".equals(method)
                && uri.matches(".*/api/v1/knowledge-bases/[^/]+/documents/uploads/[^/]+/parts/[^/]+$"));
        return !(upload || chunked);
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String kbId = request.getRequestURI().replaceAll("^.*/knowledge-bases/([^/]+)/documents.*$", "$1");
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null || !(authentication.getPrincipal() instanceof SecurityPrincipal principal)) {
            response.setStatus(401);
            response.setCharacterEncoding(StandardCharsets.UTF_8.name());
            response.setContentType(MediaType.APPLICATION_JSON_VALUE);
            response.setHeader(TraceContext.TRACE_HEADER, TraceContext.current());
            objectMapper.writeValue(response.getOutputStream(), ApiEnvelope.error(ErrorCodes.AUTH_UNAUTHENTICATED,
                    "未提供有效凭证", "authentication required", TraceContext.current()));
            return;
        }
        if (rbac.allowed(principal, "doc:upload", kbId)) {
            chain.doFilter(request, response);
            return;
        }
        audit.record(principal, "permission.denied", "knowledgeBase", kbId, "DENIED", Map.of("permission", "doc:upload"),
                request.getRemoteAddr(), abbreviate(request.getHeader("User-Agent")), TraceContext.current());
        alerts.alert("UPLOAD_PERMISSION_DENIED", "knowledgeBase", kbId, TraceContext.current(), "permission=doc:upload");
        response.setStatus(403);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setHeader(TraceContext.TRACE_HEADER, TraceContext.current());
        objectMapper.writeValue(response.getOutputStream(), ApiEnvelope.error(ErrorCodes.PERM_UPLOAD_FORBIDDEN,
                "无权向目标知识库上传文档", "real-time kb permission denied", TraceContext.current()));
    }

    private static String abbreviate(String value) {
        return value != null && value.length() > 512 ? value.substring(0, 512) : value;
    }
}
