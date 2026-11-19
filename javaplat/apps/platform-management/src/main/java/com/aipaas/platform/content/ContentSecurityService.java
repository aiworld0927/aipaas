package com.aipaas.platform.content;

import com.aipaas.platform.audit.AuditService;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.config.SecurityAlertService;
import com.aipaas.platform.content.ContentSecurityEngine.PolicyRecord;
import com.aipaas.platform.content.ContentSecurityEngine.RuleRecord;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.PatternSyntaxException;

/** 管理侧内容策略事实源与审计实现；检测算法由 common 共享。 */
@Service
public class ContentSecurityService extends ContentSecurityEngine {

    private static final Set<String> ACTIONS = Set.of("ALLOW", "BLOCK", "REPLACE", "WARN");

    private final ContentSecurityRepository repository;
    private final AuditService audit;
    private final SecurityAlertService alerts;
    private final com.aipaas.platform.observability.ObservabilityMetrics metrics;
    private final GatewayContentPolicyPublisher publisher;

    public ContentSecurityService(ContentSecurityRepository repository, AuditService audit,
                                  SecurityAlertService alerts,
                                  com.aipaas.platform.observability.ObservabilityMetrics metrics,
                                  GatewayContentPolicyPublisher publisher) {
        this.repository = repository;
        this.audit = audit;
        this.alerts = alerts;
        this.metrics = metrics;
        this.publisher = publisher;
    }

    @Override
    protected PolicyRecord policy(String type) {
        return repository.policy(type).orElse(null);
    }

    @Override
    protected List<RuleRecord> rules(String type, boolean enabledOnly) {
        return repository.rules(type, enabledOnly);
    }

    @Override
    protected void recordMetric(String type, String action, boolean passed) {
        metrics.recordSecurityCheck(type, action, passed);
    }

    @Override
    protected void auditAndAlert(CheckResult result, List<String> ruleIds) {
        String action = INPUT.equals(result.type()) ? "security.input.blocked"
                : PROMPT.equals(result.type()) ? "security.prompt_injection.blocked" : "security.output.blocked";
        SecurityPrincipal actor = SecurityPrincipal.current();
        audit.record(actor, action, "securityCheck", result.traceId(), result.passed() ? "SUCCESS" : "DENIED",
                Map.of("riskTypes", result.riskTypes(), "score", result.score(), "ruleIds", ruleIds,
                        "ruleVersion", result.ruleVersion(), "action", result.action()));
        alerts.alert(PROMPT.equals(result.type()) ? "PROMPT_INJECTION_DETECTED" : "CONTENT_POLICY_DETECTED",
                "securityCheck", result.traceId(), result.traceId(), "riskTypes=" + result.riskTypes());
    }

    public Map<String, Object> policies() {
        Map<String, PolicyRecord> policies = new LinkedHashMap<>();
        repository.policies().forEach(p -> policies.put(p.ruleType(), p));
        Map<String, Object> result = new LinkedHashMap<>();
        OffsetDateTime updatedAt = null;
        String updatedBy = null;
        for (String type : List.of(INPUT, OUTPUT, PROMPT)) {
            PolicyRecord policy = policies.get(type);
            if (policy == null) continue;
            result.put(propertyName(type), policyResponse(policy, repository.rules(type, false)));
            if (updatedAt == null || policy.updatedAt().isAfter(updatedAt)) {
                updatedAt = policy.updatedAt();
                updatedBy = policy.updatedBy();
            }
        }
        result.put("updatedAt", updatedAt);
        result.put("updatedBy", updatedBy);
        return result;
    }

    @Transactional
    public Map<String, Object> updatePolicies(Map<String, PolicyUpdate> updates, String updatedBy) {
        if (updates == null || updates.isEmpty()) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "至少更新一项安全策略");
        }
        // 先移除旧快照；若事务或发布失败，网关拒绝问答而不是沿用失效策略。
        publisher.invalidate();
        for (Map.Entry<String, PolicyUpdate> entry : updates.entrySet()) {
            String type = entry.getKey();
            PolicyUpdate update = entry.getValue();
            validateTypeAndContent(type, "validation");
            repository.policy(type).orElseThrow();
            validateUpdate(type, update);
            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
            repository.savePolicy(new PolicyRecord(type, update.enabled(), update.action(), update.threshold(),
                    update.ruleVersion(), now, updatedBy));
            if (update.rules() != null) {
                List<RuleRecord> rules = update.rules().stream().map(rule -> validateRule(type, rule)).toList();
                repository.replaceRules(type, rules);
            }
        }
        Map<String, Object> response = policies();
        publisher.publishAfterCommit();
        return response;
    }

    private static Map<String, Object> policyResponse(PolicyRecord policy, List<RuleRecord> rules) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("enabled", policy.enabled());
        value.put("action", policy.action());
        value.put("ruleVersion", policy.ruleVersion());
        value.put("threshold", policy.threshold());
        value.put("rules", rules);
        return value;
    }

    private static RuleRecord validateRule(String type, RuleRecord rule) {
        if (rule == null || rule.ruleId() == null || !rule.ruleId().matches("[A-Za-z0-9_.-]{2,64}")
                || rule.pattern() == null || rule.pattern().isBlank() || rule.pattern().length() > 512
                || rule.matchType() == null || !Set.of("REGEX", "CONTAINS").contains(rule.matchType())
                || rule.weight() < 0 || rule.weight() > 1 || (rule.description() != null && rule.description().length() > 256)) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "安全规则定义不合法");
        }
        try {
            compile(new RuleRecord(rule.ruleId(), type, rule.pattern(), rule.matchType(), rule.description(),
                    rule.enabled(), rule.weight()));
        } catch (PatternSyntaxException ex) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "安全规则正则表达式无效");
        }
        return new RuleRecord(rule.ruleId(), type, rule.pattern(), rule.matchType(), rule.description(),
                rule.enabled(), rule.weight());
    }

    private static void validateUpdate(String type, PolicyUpdate update) {
        if (update == null || update.action() == null || !ACTIONS.contains(update.action()) || update.ruleVersion() == null
                || update.ruleVersion().isBlank() || update.ruleVersion().length() > 32
                || update.threshold() != null && (update.threshold() < 0 || update.threshold() > 1)
                || INPUT.equals(type) && !"BLOCK".equals(update.action())) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "安全策略配置不合法");
        }
    }

    private static String propertyName(String type) {
        return switch (type) {
            case INPUT -> "inputContent";
            case OUTPUT -> "outputContent";
            case PROMPT -> "promptInjection";
            default -> throw new IllegalArgumentException("Unknown rule type");
        };
    }

    public record PolicyUpdate(boolean enabled, String action, String ruleVersion, Double threshold,
                               List<RuleRecord> rules) {
    }
}
