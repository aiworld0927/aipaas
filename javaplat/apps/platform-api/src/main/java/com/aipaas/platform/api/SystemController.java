package com.aipaas.platform.api;

import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.web.TraceContext;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/system")
public class SystemController {

    @Value("${aipaas.contract.version:1.1.0-p0}")
    private String contractVersion;

    @Value("${aipaas.api.version:v1}")
    private String apiVersion;

    @Value("${aipaas.env:dev}")
    private String env;

    @GetMapping("/info")
    public ApiEnvelope<Map<String, Object>> info() {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("service", "platform-api");
        data.put("version", "0.1.0");
        data.put("apiVersion", apiVersion);
        data.put("contractVersion", contractVersion);
        data.put("contractStatus", "frozen");
        data.put("env", env);
        data.put("p0FeatureCount", 44);
        data.put("domains", List.of(
                "数据接入与解析", "知识构建与索引", "检索引擎", "生成与推理", "知识治理",
                "安全与合规", "可观测与质量评估", "平台与管理", "用户界面与体验", "模型服务"));
        data.put("capabilities", List.of("RAG"));
        return ApiEnvelope.ok(data, TraceContext.current());
    }
}
