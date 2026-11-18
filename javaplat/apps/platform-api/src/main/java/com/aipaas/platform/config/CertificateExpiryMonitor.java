package com.aipaas.platform.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;

/** P0 反向代理证书到期巡检；只读取公开证书，不读取私钥。 */
@Service
public class CertificateExpiryMonitor {

    private final SecurityAlertService alerts;
    private final Path certificatePath;
    private final int warningDays;
    private final boolean enabled;
    private volatile CertificateStatus lastStatus;

    public CertificateExpiryMonitor(SecurityAlertService alerts,
                                    @Value("${aipaas.security.tls.certificate-path:/etc/aipaas/tls/tls.crt}") String certificatePath,
                                    @Value("${aipaas.security.tls.expiry-warning-days:30}") int warningDays,
                                    @Value("${aipaas.security.tls.enabled:true}") boolean enabled) {
        this.alerts = alerts;
        this.certificatePath = Path.of(certificatePath).toAbsolutePath().normalize();
        this.warningDays = warningDays;
        this.enabled = enabled;
    }

    @jakarta.annotation.PostConstruct
    void initialize() {
        checkCertificate();
    }

    @Scheduled(cron = "${aipaas.security.tls.check-cron:0 0 8 * * *}")
    public void checkCertificate() {
        inspectCertificate(true);
    }

    private void inspectCertificate(boolean emitAlert) {
        if (!enabled) {
            lastStatus = new CertificateStatus(false, "TLSv1.2", null);
            return;
        }
        if (!Files.isRegularFile(certificatePath)) {
            lastStatus = new CertificateStatus(true, "TLSv1.2", null);
            if (emitAlert) {
                alerts.alert("TLS_CERTIFICATE_UNAVAILABLE", "certificate", "reverse-proxy", "scheduled", "certificate unavailable");
            }
            return;
        }
        try (InputStream input = Files.newInputStream(certificatePath)) {
            X509Certificate certificate = (X509Certificate) CertificateFactory.getInstance("X.509")
                    .generateCertificate(input);
            Instant expiresAt = certificate.getNotAfter().toInstant();
            long days = ChronoUnit.DAYS.between(Instant.now(), expiresAt);
            lastStatus = new CertificateStatus(true, "TLSv1.2",
                    OffsetDateTime.ofInstant(expiresAt, ZoneOffset.UTC).toString());
            if (!emitAlert) return;
            if (days < 0) {
                alerts.alert("TLS_CERTIFICATE_EXPIRED", "certificate", "reverse-proxy", "scheduled",
                        "expiredAt=" + expiresAt);
            } else if (days <= warningDays) {
                alerts.alert("TLS_CERTIFICATE_EXPIRING", "certificate", "reverse-proxy", "scheduled",
                        "daysRemaining=" + days);
            }
        } catch (Exception ex) {
            lastStatus = new CertificateStatus(true, "TLSv1.2", null);
            if (emitAlert) {
                alerts.alert("TLS_CERTIFICATE_INVALID", "certificate", "reverse-proxy", "scheduled",
                        "error=" + ex.getClass().getSimpleName());
            }
        }
    }

    public CertificateStatus status() {
        // Web 容器可能晚于本平台生成开发证书，查询时刷新以避免长期缓存启动期的“不可用”状态。
        inspectCertificate(false);
        return lastStatus;
    }

    public record CertificateStatus(boolean enabled, String minVersion, String certificateExpiresAt) {
    }
}
