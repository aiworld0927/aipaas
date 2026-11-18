package com.aipaas.platform.audit;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.web.TraceContext;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;

/** 对契约声明的关键操作统一留痕，包含未通过权限校验和业务校验的请求。 */
@Component
public class AuditFilter extends OncePerRequestFilter {

    private final AuditService auditService;

    public AuditFilter(AuditService auditService) {
        this.auditService = auditService;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        Route route = Route.from(request);
        SecurityPrincipal actor = SecurityPrincipal.current();
        try {
            chain.doFilter(request, response);
        } finally {
            if (route != null) {
                String result = response.getStatus() == 403 ? "DENIED"
                        : response.getStatus() >= 200 && response.getStatus() < 300 ? "SUCCESS" : "FAILURE";
                auditService.record(actor, route.action(), route.objectType(), route.objectId(request), result,
                        Map.of("httpStatus", response.getStatus()), clientIp(request),
                        abbreviate(request.getHeader("User-Agent")), TraceContext.current());
            }
        }
    }

    private static String clientIp(HttpServletRequest request) {
        String forwarded = request.getHeader("X-Forwarded-For");
        return forwarded == null || forwarded.isBlank() ? request.getRemoteAddr() : forwarded.split(",")[0].trim();
    }

    private static String abbreviate(String value) {
        return value != null && value.length() > 512 ? value.substring(0, 512) : value;
    }

    private record Route(String action, String objectType, String prefix) {
        static Route from(HttpServletRequest request) {
            String path = request.getRequestURI();
            String method = request.getMethod();
            if ("POST".equals(method) && path.matches(".*/knowledge-bases/[^/]+/documents$"))
                return new Route("doc.upload", "knowledgeBase", "/knowledge-bases/");
            if ("POST".equals(method) && path.matches(".*/documents/uploads/[^/]+:complete$"))
                return new Route("doc.upload", "knowledgeBase", "/knowledge-bases/");
            if ("POST".equals(method) && path.matches(".*/knowledge-bases$"))
                return new Route("kb.create", "knowledgeBase", "/knowledge-bases/");
            if ("PATCH".equals(method) && path.matches(".*/knowledge-bases/[^/]+$"))
                return new Route("kb.update", "knowledgeBase", "/knowledge-bases/");
            if ("DELETE".equals(method) && path.matches(".*/knowledge-bases/[^/]+$"))
                return new Route("kb.delete", "knowledgeBase", "/knowledge-bases/");
            if ("POST".equals(method) && path.matches(".*/ingest/tasks/[^/]+:retry$"))
                return new Route("ingest.retry", "ingestTask", "/ingest/tasks/");
            if ("DELETE".equals(method) && path.matches(".*/documents/[^/]+$"))
                return new Route("doc.delete", "document", "/documents/");
            if ("GET".equals(method) && path.matches(".*/exports/[^/]+/download$"))
                return new Route("export.download", "export", "/exports/");
            if ("GET".equals(method) && path.endsWith("/audit-logs") && "csv".equalsIgnoreCase(request.getParameter("format")))
                return new Route("export.audit", "auditLog", "audit");
            if ("PUT".equals(method) && path.matches(".*/roles/[^/]+/permissions$"))
                return new Route("role.permission.update", "role", "/roles/");
            if ("POST".equals(method) && path.endsWith("/roles")) return new Route("role.create", "role", "role");
            if ("POST".equals(method) && path.endsWith("/security/policies"))
                return new Route("security.policy.update", "securityPolicy", "security");
            if ("PUT".equals(method) && path.endsWith("/security/policies"))
                return new Route("security.policy.update", "securityPolicy", "security");
            if ("POST".equals(method) && path.endsWith("/security/checks"))
                return new Route("security.check", "securityCheck", "security");
            if ("POST".equals(method) && (path.endsWith("/chat") || path.endsWith("/chat/stream")))
                return new Route("chat.query", "chat", "chat");
            if ("POST".equals(method) && path.endsWith("/models/chat"))
                return new Route("model.chat", "model", "model");
            if ("POST".equals(method) && path.endsWith("/admin/users")) return new Route("user.create", "user", "user");
            if ("PATCH".equals(method) && path.matches(".*/admin/users/[^/]+$"))
                return new Route("user.update", "user", "/admin/users/");
            if ("PUT".equals(method) && path.endsWith("/admin/system-config"))
                return new Route("config.update", "systemConfig", "config");
            return null;
        }

        String objectId(HttpServletRequest request) {
            if (prefix.equals("security") || prefix.equals("chat")
                    || prefix.equals("model") || prefix.equals("audit") || prefix.equals("user")
                    || prefix.equals("config")) return null;
            String path = request.getRequestURI();
            if (prefix.equals("role")) return null;
            int start = path.indexOf(prefix);
            if (start < 0) return null;
            String remainder = path.substring(start + prefix.length());
            return remainder.split("[/:]")[0];
        }
    }
}
