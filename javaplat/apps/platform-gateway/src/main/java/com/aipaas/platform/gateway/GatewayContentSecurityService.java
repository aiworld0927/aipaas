package com.aipaas.platform.gateway;

import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.content.ContentSecurityEngine;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;

/** 从 Redis 读取管理侧策略快照；快照缺失或不可读时 fail-closed。 */
@Service
public class GatewayContentSecurityService extends ContentSecurityEngine {

    private static final String SNAPSHOT_KEY = "aipaas:gateway:content-policies";
    private final StringRedisTemplate redis;
    private final com.fasterxml.jackson.databind.ObjectMapper objectMapper;
    private final GatewayAuditService audit;
    private final MeterRegistry meters;

    public GatewayContentSecurityService(StringRedisTemplate redis,
                                         com.fasterxml.jackson.databind.ObjectMapper objectMapper,
                                         GatewayAuditService audit, MeterRegistry meters) {
        this.redis = redis;
        this.objectMapper = objectMapper;
        this.audit = audit;
        this.meters = meters;
    }

    @Override
    protected PolicyRecord policy(String type) {
        return snapshot().policies().stream().filter(policy -> type.equals(policy.ruleType())).findFirst().orElse(null);
    }

    @Override
    protected List<RuleRecord> rules(String type, boolean enabledOnly) {
        return snapshot().rules().stream()
                .filter(rule -> type.equals(rule.ruleType()) && (!enabledOnly || rule.enabled())).toList();
    }

    private Snapshot snapshot() {
        try {
            String value = redis.opsForValue().get(SNAPSHOT_KEY);
            if (value == null || value.isBlank()) throw unavailable();
            Snapshot snapshot = objectMapper.readValue(value, Snapshot.class);
            if (snapshot.policies() == null || snapshot.rules() == null) throw unavailable();
            return snapshot;
        } catch (ApiException ex) {
            throw ex;
        } catch (Exception ex) {
            throw new ApiException(ErrorCodes.SYS_DEPENDENCY_UNAVAILABLE, HttpStatus.SERVICE_UNAVAILABLE,
                    "内容安全策略暂不可用");
        }
    }

    @Override
    protected void auditAndAlert(CheckResult result, List<String> ruleIds) {
        String action = INPUT.equals(result.type()) ? "security.input.blocked"
                : PROMPT.equals(result.type()) ? "security.prompt_injection.blocked" : "security.output.blocked";
        SecurityPrincipal actor = SecurityPrincipal.current();
        audit.record(actor, action, "securityCheck", result.traceId(), result.passed() ? "SUCCESS" : "DENIED",
                Map.of("riskTypes", result.riskTypes(), "score", result.score(), "ruleIds", ruleIds,
                        "ruleVersion", result.ruleVersion(), "action", result.action()));
        audit.alert(PROMPT.equals(result.type()) ? "PROMPT_INJECTION_DETECTED" : "CONTENT_POLICY_DETECTED",
                "securityCheck", result.traceId(), result.traceId(), "riskTypes=" + result.riskTypes());
    }

    @Override
    protected void recordMetric(String type, String action, boolean passed) {
        Counter.builder("aipaas.security.checks.total").tags("type", type, "action", action,
                "result", passed ? "PASSED" : "BLOCKED").register(meters).increment();
        if (!passed) Counter.builder("aipaas.security.blocks.total").tags("type", type).register(meters).increment();
    }

    private static ApiException unavailable() {
        return new ApiException(ErrorCodes.SYS_DEPENDENCY_UNAVAILABLE, HttpStatus.SERVICE_UNAVAILABLE,
                "内容安全策略暂不可用");
    }
}
