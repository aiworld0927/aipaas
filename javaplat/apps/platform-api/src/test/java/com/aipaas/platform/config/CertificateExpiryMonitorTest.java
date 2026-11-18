package com.aipaas.platform.config;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

class CertificateExpiryMonitorTest {

    @TempDir
    Path temp;

    @Test
    void missingCertificateAlertsOnlyOnScheduledCheckAndStatusCanRefresh() throws Exception {
        SecurityAlertService alerts = mock(SecurityAlertService.class);
        Path certificate = temp.resolve("tls.crt");
        CertificateExpiryMonitor monitor = new CertificateExpiryMonitor(alerts, certificate.toString(), 30, true);

        assertNull(monitor.status().certificateExpiresAt());
        verify(alerts, never()).alert(anyString(), anyString(), anyString(), anyString(), anyString());
        monitor.checkCertificate();
        verify(alerts).alert("TLS_CERTIFICATE_UNAVAILABLE", "certificate", "reverse-proxy", "scheduled",
                "certificate unavailable");

        Files.writeString(certificate, "not a certificate");
        assertNull(monitor.status().certificateExpiresAt());
        monitor.checkCertificate();
        verify(alerts).alert(eq("TLS_CERTIFICATE_INVALID"), eq("certificate"), eq("reverse-proxy"), eq("scheduled"), anyString());
    }

    @Test
    void disabledMonitorDoesNotReadOrAlert() {
        SecurityAlertService alerts = mock(SecurityAlertService.class);
        CertificateExpiryMonitor monitor = new CertificateExpiryMonitor(alerts,
                temp.resolve("missing.crt").toString(), 30, false);

        assertFalse(monitor.status().enabled());
        monitor.checkCertificate();
        verifyNoInteractions(alerts);
    }
}
