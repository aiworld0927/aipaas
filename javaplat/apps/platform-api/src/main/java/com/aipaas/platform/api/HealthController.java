package com.aipaas.platform.api;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/** 基础设施探针：不鉴权、不包信封、不写审计；Docker/K8s 直接依赖。 */
@RestController
public class HealthController {

    @GetMapping("/healthz")
    public Map<String, Object> healthz() {
        return Map.of("status", "UP");
    }

    @GetMapping("/readyz")
    public Map<String, Object> readyz() {
        Map<String, Object> checks = new LinkedHashMap<>();
        checks.put("config", Map.of("status", "UP"));
        checks.put("contract", Map.of("status", "UP"));
        return Map.of("status", "UP", "checks", checks);
    }
}
