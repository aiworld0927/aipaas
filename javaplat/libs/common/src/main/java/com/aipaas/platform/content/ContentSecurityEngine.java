package com.aipaas.platform.content;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import org.springframework.http.HttpStatus;

import java.text.Normalizer;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** 两应用共享的内容安全算法；存储、审计、告警和指标由各应用提供。 */
public abstract class ContentSecurityEngine {

    public static final String INPUT = "INPUT_CONTENT";
    public static final String OUTPUT = "OUTPUT_CONTENT";
    public static final String PROMPT = "PROMPT_INJECTION";
    protected static final Set<String> TYPES = Set.of(INPUT, OUTPUT, PROMPT);

    protected abstract PolicyRecord policy(String type);

    protected abstract List<RuleRecord> rules(String type, boolean enabledOnly);

    protected abstract void auditAndAlert(CheckResult result, List<String> ruleIds);

    protected abstract void recordMetric(String type, String action, boolean passed);

    public CheckResult check(String type, String content) {
        validateTypeAndContent(type, content);
        PolicyRecord policy = policy(type);
        if (policy == null) throw new IllegalStateException("Missing content security policy: " + type);
        if (!policy.enabled()) {
            CheckResult allowed = new CheckResult(type, true, "ALLOW", List.of(), 0, null,
                    policy.ruleVersion(), TraceContext.current());
            recordMetric(type, allowed.action(), true);
            return allowed;
        }
        String normalized = normalize(content);
        List<RuleMatch> matches = new ArrayList<>();
        for (RuleRecord rule : rules(type, true)) {
            Matcher matcher = compile(rule).matcher(normalized);
            if (matcher.find()) matches.add(new RuleMatch(rule));
        }
        double score = PROMPT.equals(type)
                ? Math.min(1, matches.stream().mapToDouble(m -> m.rule().weight()).sum())
                : matches.isEmpty() ? 0 : 1;
        double threshold = policy.threshold() == null ? 1 : policy.threshold();
        boolean detected = !matches.isEmpty() && (!PROMPT.equals(type) || score >= threshold);
        if (!detected) {
            CheckResult allowed = new CheckResult(type, true, "ALLOW", List.of(), score, null,
                    policy.ruleVersion(), TraceContext.current());
            recordMetric(type, allowed.action(), true);
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
        recordMetric(type, result.action(), result.passed());
        auditAndAlert(result, ruleIds);
        return result;
    }

    /** REPLACE 基于原文回写，避免把回答意外转换为小写。 */
    public CheckResult checkOutput(String content) {
        CheckResult result = check(OUTPUT, content);
        if (result.passed() && "REPLACE".equals(result.action()) && content != null) {
            String sanitized = content;
            for (RuleRecord rule : rules(OUTPUT, true)) {
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
        if (!input.passed()) throw blocked(ErrorCodes.BIZ_CONTENT_BLOCKED, "输入内容违反安全规则");
        CheckResult prompt = check(PROMPT, query);
        if (!prompt.passed()) throw blocked(ErrorCodes.BIZ_PROMPT_INJECTION, "检测到 Prompt 注入或越狱风险");
        return prompt.sanitizedContent() == null ? query : prompt.sanitizedContent();
    }

    protected static Pattern compile(RuleRecord rule) {
        String regex = "CONTAINS".equals(rule.matchType()) ? Pattern.quote(rule.pattern()) : rule.pattern();
        return Pattern.compile(regex, Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE | Pattern.DOTALL);
    }

    protected static void validateTypeAndContent(String type, String content) {
        if (type == null || !TYPES.contains(type) || content == null || content.length() > 20000) {
            throw new ApiException(ErrorCodes.PARAM_INVALID, HttpStatus.BAD_REQUEST, "检查类型或内容长度不合法");
        }
    }

    private static ApiException blocked(String code, String message) {
        return new ApiException(code, HttpStatus.UNPROCESSABLE_ENTITY, message);
    }

    private static String sanitize(String normalized, List<RuleMatch> matches) {
        String result = normalized;
        for (RuleMatch match : matches) result = compile(match.rule()).matcher(result).replaceAll("[已净化]");
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

    public record PolicyRecord(String ruleType, boolean enabled, String action, Double threshold,
                               String ruleVersion, OffsetDateTime updatedAt, String updatedBy) {
    }

    public record RuleRecord(String ruleId, String ruleType, String pattern, String matchType,
                             String description, boolean enabled, double weight) {
    }

    public record Snapshot(List<PolicyRecord> policies, List<RuleRecord> rules) {
    }

    public record CheckResult(String type, boolean passed, String action, List<String> riskTypes, double score,
                              String sanitizedContent, String ruleVersion, String traceId) {
    }

    private record RuleMatch(RuleRecord rule) {
    }
}
