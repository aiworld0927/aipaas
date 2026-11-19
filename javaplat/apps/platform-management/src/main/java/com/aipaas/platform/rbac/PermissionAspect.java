package com.aipaas.platform.rbac;

import com.aipaas.platform.audit.AuditService;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.config.SecurityAlertService;
import jakarta.servlet.http.HttpServletRequest;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.annotation.Before;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;
import org.springframework.web.servlet.HandlerMapping;

import java.util.Map;

@Aspect
@Component
public class PermissionAspect {

    private final RbacService rbac;
    private final AuditService audit;
    private final SecurityAlertService alerts;

    public PermissionAspect(RbacService rbac, AuditService audit, SecurityAlertService alerts) {
        this.rbac = rbac;
        this.audit = audit;
        this.alerts = alerts;
    }

    @Before("@annotation(required)")
    public void check(RequirePermission required) {
        SecurityPrincipal principal = SecurityPrincipal.current();
        String kbId = null;
        if (!required.kbId().isBlank()) {
            ServletRequestAttributes attributes = (ServletRequestAttributes) RequestContextHolder.getRequestAttributes();
            HttpServletRequest request = attributes == null ? null : attributes.getRequest();
            if (request != null) {
                Object variables = request.getAttribute(HandlerMapping.URI_TEMPLATE_VARIABLES_ATTRIBUTE);
                if (variables instanceof Map<?, ?> map) {
                    Object value = map.get(required.kbId());
                    kbId = value == null ? null : value.toString();
                }
            }
        }
        if (!rbac.allowed(principal, required.value(), kbId)) {
            String objectType = kbId == null ? "permission" : "knowledgeBase";
            String objectId = kbId == null ? required.value() : kbId;
            audit.record(principal, "permission.denied", objectType, objectId, "DENIED",
                    Map.of("permission", required.value()));
            alerts.alert("PERMISSION_DENIED", objectType, objectId, TraceContext.current(),
                    "permission=" + required.value());
            String code = "doc:upload".equals(required.value()) ? ErrorCodes.PERM_UPLOAD_FORBIDDEN : ErrorCodes.PERM_FORBIDDEN;
            throw new ApiException(code, HttpStatus.FORBIDDEN, "无权执行该操作");
        }
    }
}
