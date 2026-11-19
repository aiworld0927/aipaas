package com.aipaas.platform.gateway;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
public class GatewayHealthController {

    @GetMapping("/healthz")
    public Map<String, String> healthz() {
        return Map.of("status", "UP");
    }

    @GetMapping("/readyz")
    public Map<String, String> readyz() {
        return Map.of("status", "UP");
    }
}
