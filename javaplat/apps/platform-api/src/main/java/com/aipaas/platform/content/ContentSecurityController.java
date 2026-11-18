package com.aipaas.platform.content;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.content.ContentSecurityRepository.RuleRecord;
import com.aipaas.platform.content.ContentSecurityService.PolicyUpdate;
import com.aipaas.platform.rbac.RequirePermission;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/security")
public class ContentSecurityController {

    private final ContentSecurityService security;

    public ContentSecurityController(ContentSecurityService security) {
        this.security = security;
    }

    @GetMapping("/policies")
    @RequirePermission("security:read")
    public ApiEnvelope<Map<String, Object>> policies() {
        return ApiEnvelope.ok(security.policies(), TraceContext.current());
    }

    @PutMapping("/policies")
    @RequirePermission("security:write")
    public ApiEnvelope<Map<String, Object>> update(@RequestBody PolicyUpdateRequest request) {
        Map<String, PolicyUpdate> updates = new LinkedHashMap<>();
        if (request.inputContent() != null) updates.put(ContentSecurityService.INPUT, request.inputContent());
        if (request.outputContent() != null) updates.put(ContentSecurityService.OUTPUT, request.outputContent());
        if (request.promptInjection() != null) updates.put(ContentSecurityService.PROMPT, request.promptInjection());
        return ApiEnvelope.ok(security.updatePolicies(updates, SecurityPrincipal.current().userId()), TraceContext.current());
    }

    @PostMapping("/checks")
    @RequirePermission("security:check")
    public ApiEnvelope<ContentSecurityService.CheckResult> check(@Valid @RequestBody CheckRequest request) {
        return ApiEnvelope.ok(security.check(request.type(), request.content()), TraceContext.current());
    }

    public record CheckRequest(@NotBlank String type, @NotNull @Size(max = 20000) String content) {
    }

    public record PolicyUpdateRequest(PolicyUpdate inputContent, PolicyUpdate outputContent,
                                      PolicyUpdate promptInjection) {
        public PolicyUpdateRequest {
            if (inputContent == null && outputContent == null && promptInjection == null) {
                throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "至少更新一项安全策略");
            }
        }
    }
}
