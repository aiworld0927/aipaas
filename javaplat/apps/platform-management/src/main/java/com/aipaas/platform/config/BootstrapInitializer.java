package com.aipaas.platform.config;

import com.aipaas.platform.auth.AuthService;
import com.aipaas.platform.content.ContentSecurityEngine.RuleRecord;
import com.aipaas.platform.rbac.RoleRepository;
import com.aipaas.platform.rbac.RoleRepository.Grant;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;

/** 幂等初始化 P0 安全目录；不输出任何口令、密钥或令牌。 */
@Component
public class BootstrapInitializer implements ApplicationRunner {

    private final JdbcTemplate jdbc;
    private final RoleRepository roles;
    private final AuthService auth;
    private final String adminPassword;

    public BootstrapInitializer(JdbcTemplate jdbc, RoleRepository roles, AuthService auth,
                                @Value("${aipaas.bootstrap.admin-password:}") String adminPassword) {
        this.jdbc = jdbc;
        this.roles = roles;
        this.auth = auth;
        this.adminPassword = adminPassword;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        seedPermissions();
        seedKnowledgeBase();
        seedRoles();
        seedPolicies();
        if (count("sec_users", "tenant_id='default'") == 0) {
            if (adminPassword == null || adminPassword.isBlank()) {
                throw new IllegalStateException("AIPAAS_BOOTSTRAP_ADMIN_PASSWORD is required for first startup");
            }
            auth.createUser("default", "admin", "平台管理员", adminPassword, null, List.of("PLATFORM_ADMIN"));
        }
    }

    private void seedPermissions() {
        record P(String code, String name, String domain) {}
        List<P> permissions = List.of(
                new P("user:write", "创建用户", "平台"), new P("user:read", "查询用户", "平台"),
                new P("tenant:read", "查询租户", "平台"), new P("tenant:write", "管理租户", "平台"),
                new P("role:read", "查询角色", "权限"),
                new P("role:write", "修改角色", "权限"), new P("audit:read", "检索导出审计", "审计"),
                new P("config:read", "查询系统配置", "平台"), new P("config:write", "修改系统配置", "平台"),
                new P("admin:read", "查询管理总览", "平台"),
                new P("security:read", "查询安全策略", "安全"), new P("security:write", "修改安全策略", "安全"),
                new P("security:check", "执行内容检查", "安全"), new P("kb:read", "查询知识库", "知识库"),
                new P("kb:write", "管理知识库", "知识库"), new P("kb:manage", "知识库级管理授权", "知识库"),
                new P("doc:read", "查询文档", "文档"), new P("doc:upload", "上传文档", "文档"),
                new P("doc:delete", "删除文档", "文档"), new P("doc:write", "修改文档", "文档"),
                new P("ingest:read", "查询接入任务", "文档"), new P("ingest:retry", "重试接入任务", "文档"),
                new P("index:read", "查询向量索引", "索引"), new P("index:build", "构建/回退向量索引", "索引"),
                new P("retrieval:query", "检索知识库", "检索"),
                new P("chat:use", "知识问答", "问答"),
                new P("model:invoke", "调用模型", "模型"), new P("export:read", "查询导出", "导出"),
                new P("export:create", "创建导出任务", "导出"),
                new P("export:download", "下载导出", "导出"), new P("obs:read", "查询可观测数据", "可观测"));
        for (P p : permissions) {
            if (jdbc.queryForObject("select count(*) from sec_permissions where tenant_id='default' and code=?",
                    Integer.class, p.code()) == 0) {
                jdbc.update("insert into sec_permissions(tenant_id,code,name,domain,description) values('default',?,?,?,?)",
                        p.code(), p.name(), p.domain(), "P0 安全底座权限点");
            }
        }
    }

    private void seedKnowledgeBase() {
        if (count("knowledge_bases", "id='kb_default'") == 0) {
            OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
            jdbc.update("insert into knowledge_bases(id,tenant_id,name,status,created_at,updated_at) values(?,?,?,?,?,?)",
                    "kb_default", "default", "默认金融标准库", "ACTIVE", now, now);
        }
    }

    private void seedRoles() {
        seedRole("rol_platform_admin", "PLATFORM_ADMIN", "平台管理员", List.of("*"), List.of());
        seedRole("rol_kb_admin", "KB_ADMIN", "知识库管理员",
                List.of("kb:read", "kb:write", "kb:manage", "doc:read", "doc:upload", "doc:delete", "doc:write",
                        "ingest:read", "ingest:retry", "index:read", "index:build", "retrieval:query",
                        "chat:use", "security:check", "export:create", "export:read", "export:download"),
                List.of(new Grant("kb:manage", "KB", "kb_default"),
                        new Grant("index:read", "KB", "kb_default"),
                        new Grant("index:build", "KB", "kb_default"),
                        new Grant("retrieval:query", "KB", "kb_default")));
        seedRole("rol_kb_uploader", "KB_UPLOADER", "知识库上传员", List.of("kb:read", "doc:read", "doc:upload"),
                List.of(new Grant("doc:upload", "KB", "kb_default")));
        seedRole("rol_auditor", "AUDITOR", "安全审计员", List.of("audit:read", "security:read", "obs:read"), List.of());
    }

    private void seedRole(String id, String code, String name, List<String> permissions, List<Grant> grants) {
        if (count("sec_roles", "tenant_id='default' and code='" + code + "'") != 0) return;
        roles.createRole(id, "default", code, name, true);
        roles.replaceMatrix("default", id, permissions, grants);
    }

    private void seedPolicies() {
        seedPolicy("INPUT_CONTENT", "BLOCK", 1.0);
        seedPolicy("OUTPUT_CONTENT", "REPLACE", 1.0);
        seedPolicy("PROMPT_INJECTION", "BLOCK", 0.8);
        List<RuleRecord> input = new ArrayList<>();
        input.add(rule("in_forbidden_sample", "INPUT_CONTENT", "违禁词示例", "CONTAINS", "验收用敏感词"));
        input.add(rule("in_terror", "INPUT_CONTENT", "恐怖主义|暴恐袭击|制作爆炸物", "REGEX", "暴恐违规内容"));
        input.add(rule("in_gambling", "INPUT_CONTENT", "赌博网站|网络赌场|色情服务", "REGEX", "违法违规内容"));
        input.add(rule("in_private_secret", "INPUT_CONTENT", "内部绝密|禁止外发的商业秘密", "REGEX", "敏感信息样例"));
        input.forEach(this::seedRule);
        input.forEach(r -> seedRule(new RuleRecord(r.ruleId().replace("in_", "out_"), "OUTPUT_CONTENT", r.pattern(),
                r.matchType(), r.description(), r.enabled(), r.weight())));
        promptRules().forEach(this::seedRule);
    }

    private List<RuleRecord> promptRules() {
        return List.of(
                rule("pi_ignore_previous", "PROMPT_INJECTION",
                        "(ignore|disregard|forget|override|bypass).{0,35}(previous|prior|above|preceding|system|developer|all).{0,25}(instructions?|prompts?|rules?|messages?|restrictions?)",
                        "REGEX", "忽略或覆盖既有指令"),
                rule("pi_ignore_previous_zh", "PROMPT_INJECTION",
                        "(忽略|无视|忘掉|忘记|覆盖|绕过).{0,20}(之前|先前|以上|上述|系统|开发者|原始|所有).{0,15}(指令|指示|提示词|规则|要求|限制)",
                        "REGEX", "中文忽略既有指令"),
                rule("pi_system_prompt_exfil", "PROMPT_INJECTION",
                        "(reveal|show|print|output|leak|expose|repeat).{0,35}(system prompts?|hidden instructions?|developer messages?|initial prompts?|internal rules?)",
                        "REGEX", "窃取系统提示词"),
                rule("pi_system_prompt_exfil_zh", "PROMPT_INJECTION",
                        "(显示|输出|打印|泄露|告诉我|复述|公开).{0,20}(系统提示词|开发者指令|隐藏指令|初始提示词|内部规则)",
                        "REGEX", "中文窃取系统提示词"),
                rule("pi_system_prompt_exfil_reverse_zh", "PROMPT_INJECTION",
                        "(系统提示词|开发者指令|隐藏指令|初始提示词|内部规则).{0,15}(是什么|告诉我|显示|输出|打印|泄露|复述)",
                        "REGEX", "中文反向询问系统提示"),
                rule("pi_jailbreak_dan", "PROMPT_INJECTION",
                        "\\b(do anything now|dan mode|jailbreak(ing)?|developer mode|god mode|no restrictions mode)\\b",
                        "REGEX", "DAN 与越狱模式"),
                rule("pi_jailbreak_dan_zh", "PROMPT_INJECTION",
                        "(你现在是|进入|切换到|开启|扮演).{0,15}(dan|开发者模式|无限制模式|越狱模式|上帝模式)",
                        "REGEX", "中文越狱角色"),
                rule("pi_disable_safety", "PROMPT_INJECTION",
                        "(ignore|bypass|disable|remove|turn off).{0,30}(safety|security|content|guardrail|filter|restriction|policy).{0,25}(filters?|policies|rules?|restrictions?|guardrails?)?",
                        "REGEX", "绕过安全控制"),
                rule("pi_disable_safety_zh", "PROMPT_INJECTION",
                        "(无需|不要|不能|停止|绕过|关闭|解除).{0,12}(遵守|受|遵循|安全|内容|平台|系统|过滤|审查).{0,18}(规则|限制|策略|约束|过滤|审查|机制)?",
                        "REGEX", "中文绕过安全控制"),
                rule("pi_unrestricted_roleplay", "PROMPT_INJECTION",
                        "(act as|pretend to be|role.?play|you are now).{0,35}(unrestricted|unfiltered|no filters|evil|dan|jailbreak|anything)",
                        "REGEX", "无限制角色扮演"),
                rule("pi_fake_system_role", "PROMPT_INJECTION",
                        "(<\\|im_start\\|>\\s*(system|developer)|\\[\\s*(system|developer)\\s*\\]\\s*(ignore|override|you must)|###\\s*(system|developer)\\s*:)",
                        "REGEX", "伪造系统或开发者消息"),
                rule("pi_new_instructions", "PROMPT_INJECTION",
                        "(new|real|actual|true).{0,15}(system|developer|highest.priority).{0,20}(instructions?|rules?|commands?)",
                        "REGEX", "伪造高优先级新指令"),
                rule("pi_obey_only", "PROMPT_INJECTION",
                        "(from now on|for the rest of this conversation).{0,35}(ignore|obey only|without restrictions|no longer follow)",
                        "REGEX", "后续对话指令劫持"));
    }

    private void seedPolicy(String type, String action, double threshold) {
        if (count("sec_policies", "rule_type='" + type + "'") == 0) {
            jdbc.update("insert into sec_policies(rule_type,enabled,action,threshold,rule_version,updated_at,updated_by) "
                            + "values(?,true,?,?,'p0-1',?,'system')", type, action, threshold,
                    OffsetDateTime.now(ZoneOffset.UTC));
        }
    }

    private void seedRule(RuleRecord rule) {
        if (count("sec_rules", "rule_id='" + rule.ruleId() + "'") == 0) {
            jdbc.update("insert into sec_rules(rule_id,rule_type,pattern,match_type,description,enabled,weight) "
                            + "values(?,?,?,?,?,?,?)", rule.ruleId(), rule.ruleType(), rule.pattern(), rule.matchType(),
                    rule.description(), rule.enabled(), rule.weight());
        }
    }

    private static RuleRecord rule(String id, String type, String pattern, String matchType, String description) {
        return new RuleRecord(id, type, pattern, matchType, description, true, 1.0);
    }

    private int count(String table, String condition) {
        Integer value = jdbc.queryForObject("select count(*) from " + table + " where " + condition, Integer.class);
        return value == null ? 0 : value;
    }
}
