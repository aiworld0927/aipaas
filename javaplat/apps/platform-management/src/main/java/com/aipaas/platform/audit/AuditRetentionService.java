package com.aipaas.platform.audit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
public class AuditRetentionService {

    private static final Logger log = LoggerFactory.getLogger(AuditRetentionService.class);
    private final AuditRepository repository;
    private final int retentionDays;
    private final boolean enabled;

    public AuditRetentionService(AuditRepository repository,
                                 @Value("${aipaas.security.audit-retention-days:180}") int retentionDays,
                                 @Value("${aipaas.security.audit-retention-enabled:true}") boolean enabled) {
        if (retentionDays < 180) {
            throw new IllegalArgumentException("审计保留期不得小于 180 天");
        }
        this.repository = repository;
        this.retentionDays = retentionDays;
        this.enabled = enabled;
    }

    @Scheduled(cron = "${aipaas.security.audit-retention-cron:0 30 3 * * *}")
    public void purgeExpired() {
        if (!enabled) return;
        int deleted = repository.deleteExpired(retentionDays);
        if (deleted > 0) {
            log.info("audit retention purge completed: expiredRows={}", deleted);
        }
    }
}
