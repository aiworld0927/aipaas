package com.aipaas.platform.content;

import com.aipaas.platform.audit.AuditService;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.config.SecurityAlertService;
import com.aipaas.platform.content.ContentSecurityRepository.PolicyRecord;
import com.aipaas.platform.content.ContentSecurityRepository.RuleRecord;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.text.Normalizer;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.regex.PatternSyntaxException;

/** 内容层防护；不实现 WAF、SQLi 或 XSS 等 P1 请求层能力。 */
@Service
public class ContentSecurityService {

    public static final String INPUT = "INPUT_CONTENT";
    public static final String OUTPUT = "OUTPUT_CONTENT";
    public static final String PROMPT = "PROMPT_INJECTION";
    private static final Set<String> TYPES = Set.of(INPUT, OUTPUT, PROMPT);
    private static final Set<String> ACTIONS = Set.of("ALLOW", "BLOCK", "REPLACE", "WARN");

    private final ContentSecurityRepository repository;
    private final AuditService audit;
    private final SecurityAlertService alerts;
    private final com.aipaas.platform.observability.ObservabilityMetrics metrics;

    public ContentSecurityService(ContentSecurityRepository repository, AuditService audit,
                                  SecurityAlertService alerts,
                                  com.aipaas.platform.observability.ObservabilityMetrics metrics) {
        this.repository = repository;
        this.audit = audit;
        this.alerts = alerts;
        this.metrics = metrics;
    }

    public CheckResult check(String type, String content) {
        validateTypeAndContent(type, content);
        PolicyRecord policy = repository.policy(type)
                .orElseThrow(() -> new IllegalStateException("Missing content security policy: " + type));
        if (!policy.enabled()) {
            CheckResult allowed = new CheckResult(type, true, "ALLOW", List.of(), 0, null,
                    policy.ruleVersion(), TraceContext.current());
            metrics.recordSecurityCheck(type, allowed.action(), true);
            return allowed;
        }
        String normalized = normalize(content);
        List<RuleMatch> matches = new ArrayList<>();
        for (RuleRecord rule : repository.rules(type, true)) {
            Matcher matcher = compile(rule).matcher(normalized);
            if (matcher.find()) {
                matches.add(new RuleMatch(rule));
            }
        }
        double score = PROMPT.equals(type)
                ? Math.min(1, matches.stream().mapToDouble(m -> m.rule().weight()).sum())
                : matches.isEmpty() ? 0 : 1;
        double threshold = policy.threshold() == null ? 1 : policy.threshold();
        boolean detected = !matches.isEmpty() && (!PROMPT.equals(type) || score >= threshold);
        if (!detected) {
            CheckResult allowed = new CheckResult(type, true, "ALLOW", List.of(), score, null,
                    policy.ruleVersion(), TraceContext.current());
            metrics.recordSecurityCheck(type, allowed.action(), true);
            return allowed;
        }
        Set<String> risks = new LinkedHashSet<>();
        List<String> ruleIds = new ArrayList<>();
        for (RuleMatch match : matches) {
            risks.add(riskType(type, match.rule()));
            ruleIds.add(match.rule().ruleId());
        }
        String sanitized = "REPLACE".equals(policy.action()) ? sanitize(normalized, matches) : null;
        boolean passed = !"BLOCK".equals(policy.action());
        CheckResult result = new CheckResult(type, passed, policy.action(), List.copyOf(risks), score, sanitized,
                policy.ruleVersion(), TraceContext.current());
        metrics.recordSecurityCheck(type, result.action(), result.passed());
        auditAndAlert(result, ruleIds);
        return result;
    }

    /**
     * #157 输出合规：返回的 ``sanitizedContent`` 基于原文替换（不做小写归一），
     * 可直接回写响应；BLOCK 时调用方必须丢弃原始输出。
     */
    public CheckResult checkOutput(String content) {
        CheckResult result = check(OUTPUT, content);
        if (result.passed() && "REPLACE".equals(result.action()) && content != null) {
            String sanitized = content;
            for (RuleRecord rule : repository.rules(OUTPUT, true)) {
                sanitized = compile(rule).matcher(sanitized).replaceAll("[已净化]");
            }
            return new CheckResult(result.type(), true, result.action(), result.riskTypes(), result.score(),
                    sanitized, result.ruleVersion(), result.traceId());
        }
        return result;
    }

    /** 固定在问答入口调用：先违规内容，后 Prompt 注入；阻断时不调用下游模型。 */
    public String secureChatQuery(String query) {
        CheckResult input = check(INPUT, query);
        if (!input.passed()) {
            throw blocked(ErrorCodes.BIZ_CONTENT_BLOCKED, "输入内容违反安全规则");
        }
        CheckResult prompt = check(PROMPT, query);
        if (!prompt.passed()) {
            throw blocked(ErrorCodes.BIZ_PROMPT_INJECTION, "检测到 Prompt 注入或越狱风险");
        }
        return prompt.sanitizedContent() == null ? query : prompt.sanitizedContent();
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
        for (Map.Entry<String, PolicyUpdate> entry : updates.entrySet()) {
            String type = entry.getKey();
            PolicyUpdate update = entry.getValue();
            validateTypeAndContent(type, "validation");
            PolicyRecord current = repository.policy(type).orElseThrow();
            validateUpdate(type, update);
            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
            repository.savePolicy(new PolicyRecord(type, update.enabled(), update.action(), update.threshold(),
                    update.ruleVersion(), now, updatedBy));
            if (update.rules() != null) {
                List<RuleRecord> rules = update.rules().stream().map(rule -> validateRule(type, rule)).toList();
                repository.replaceRules(type, rules);
            }
        }
        return policies();
    }

    private void auditAndAlert(CheckResult result, List<String> ruleIds) {
        String action = INPUT.equals(result.type()) ? "security.input.blocked"
                : PROMPT.equals(result.type()) ? "security.prompt_injection.blocked" : "security.output.blocked";
        SecurityPrincipal actor = SecurityPrincipal.current();
        audit.record(actor, action, "securityCheck", result.traceId(), result.passed() ? "SUCCESS" : "DENIED",
                Map.of("riskTypes", result.riskTypes(), "score", result.score(), "ruleIds", ruleIds,
                        "ruleVersion", result.ruleVersion(), "action", result.action()));
        alerts.alert(PROMPT.equals(result.type()) ? "PROMPT_INJECTION_DETECTED" : "CONTENT_POLICY_DETECTED",
                "securityCheck", result.traceId(), result.traceId(), "riskTypes=" + result.riskTypes());
    }

    private static ApiException blocked(String code, String message) {
        return new ApiException(code, HttpStatus.UNPROCESSABLE_ENTITY, message);
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

    private static void validateTypeAndContent(String type, String content) {
        if (type == null || !TYPES.contains(type) || content == null || content.length() > 20000) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "检查类型或内容长度不合法");
        }
    }

    private static Pattern compile(RuleRecord rule) {
        String regex = "CONTAINS".equals(rule.matchType()) ? Pattern.quote(rule.pattern()) : rule.pattern();
        return Pattern.compile(regex, Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE | Pattern.DOTALL);
    }

    private static String sanitize(String normalized, List<RuleMatch> matches) {
        String result = normalized;
        for (RuleMatch match : matches) {
            result = compile(match.rule()).matcher(result).replaceAll("[已净化]");
        }
        return result;
    }

    static String normalize(String content) {
        String normalized = Normalizer.normalize(content, Normalizer.Form.NFKC).toLowerCase(Locale.ROOT);
        return normalized.replaceAll("[\\p{Cf}\\u200B-\\u200D\\uFEFF]", "").replaceAll("\\s+", " ").trim();
    }

    private static String riskType(String type, RuleRecord rule) {
        if (INPUT.equals(type) || OUTPUT.equals(type)) return "SENSITIVE_WORD";
        String id = rule.ruleId().toLowerCase(Locale.ROOT);
        return id.contains("jailbreak") || id.contains("dan") ? "JAILBREAK" : "PROMPT_INJECTION";
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

    public record CheckResult(String type, boolean passed, String action, List<String> riskTypes, double score,
                              String sanitizedContent, String ruleVersion, String traceId) {
    }

    private record RuleMatch(RuleRecord rule) {
    }
}
