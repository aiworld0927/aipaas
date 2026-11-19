package com.aipaas.platform.content;

import com.aipaas.platform.content.ContentSecurityEngine.PolicyRecord;
import com.aipaas.platform.content.ContentSecurityEngine.RuleRecord;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public class ContentSecurityRepository {

    private final JdbcTemplate jdbc;

    public ContentSecurityRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    private final RowMapper<PolicyRecord> policyMapper = (rs, n) -> new PolicyRecord(
            rs.getString("rule_type"), rs.getBoolean("enabled"), rs.getString("action"),
            (Double) rs.getObject("threshold"), rs.getString("rule_version"),
            rs.getObject("updated_at", OffsetDateTime.class), rs.getString("updated_by"));

    private final RowMapper<RuleRecord> ruleMapper = (rs, n) -> new RuleRecord(
            rs.getString("rule_id"), rs.getString("rule_type"), rs.getString("pattern"),
            rs.getString("match_type"), rs.getString("description"), rs.getBoolean("enabled"),
            rs.getDouble("weight"));

    public List<PolicyRecord> policies() {
        return jdbc.query("select * from sec_policies order by rule_type", policyMapper);
    }

    public Optional<PolicyRecord> policy(String type) {
        return jdbc.query("select * from sec_policies where rule_type=?", policyMapper, type).stream().findFirst();
    }

    public List<RuleRecord> rules(String type, boolean enabledOnly) {
        String sql = "select * from sec_rules where rule_type=?" + (enabledOnly ? " and enabled=true" : "")
                + " order by rule_id";
        return jdbc.query(sql, ruleMapper, type);
    }

    public void savePolicy(PolicyRecord policy) {
        jdbc.update("update sec_policies set enabled=?,action=?,threshold=?,rule_version=?,updated_at=?,updated_by=? "
                        + "where rule_type=?", policy.enabled(), policy.action(), policy.threshold(), policy.ruleVersion(),
                policy.updatedAt(), policy.updatedBy(), policy.ruleType());
    }

    public void replaceRules(String type, List<RuleRecord> rules) {
        jdbc.update("delete from sec_rules where rule_type=?", type);
        jdbc.batchUpdate("insert into sec_rules(rule_id,rule_type,pattern,match_type,description,enabled,weight) "
                        + "values(?,?,?,?,?,?,?)", rules, 100, (ps, rule) -> {
            ps.setString(1, rule.ruleId());
            ps.setString(2, type);
            ps.setString(3, rule.pattern());
            ps.setString(4, rule.matchType());
            ps.setString(5, rule.description());
            ps.setBoolean(6, rule.enabled());
            ps.setDouble(7, rule.weight());
        });
    }

}
