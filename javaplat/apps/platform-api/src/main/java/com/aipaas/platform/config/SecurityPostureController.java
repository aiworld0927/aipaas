package com.aipaas.platform.config;

import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.web.TraceContext;
import com.aipaas.platform.crypto.KeyringService;
import com.aipaas.platform.rbac.RequirePermission;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Map;

@RestController
@RequestMapping("/api/v1/security")
public class SecurityPostureController {

    private final CertificateExpiryMonitor certificates;
    private final KeyringService keyring;

    public SecurityPostureController(CertificateExpiryMonitor certificates, KeyringService keyring) {
        this.certificates = certificates;
        this.keyring = keyring;
    }

    @GetMapping("/posture")
    @RequirePermission("security:read")
    public ApiEnvelope<Map<String, Object>> posture() {
        CertificateExpiryMonitor.CertificateStatus tls = certificates.status();
        Map<String, Object> tlsData = new java.util.LinkedHashMap<>();
        tlsData.put("enabled", tls.enabled());
        tlsData.put("minVersion", tls.minVersion());
        tlsData.put("certificateExpiresAt", tls.certificateExpiresAt());
        Map<String, Object> data = Map.of(
                "tls", tlsData,
                "storage", Map.of("encryption", true, "algorithm", "AES-256-GCM",
                        "keyManagement", "central-keyring-0600", "keyRotationEnabled", true,
                        "lastRotatedAt", keyring.lastRotatedAt()),
                "checkedAt", OffsetDateTime.now(ZoneOffset.UTC).toString());
        return ApiEnvelope.ok(data, TraceContext.current());
    }
}
