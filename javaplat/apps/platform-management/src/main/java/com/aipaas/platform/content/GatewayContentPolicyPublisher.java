package com.aipaas.platform.content;

import com.aipaas.platform.content.ContentSecurityEngine.RuleRecord;
import com.aipaas.platform.content.ContentSecurityEngine.Snapshot;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;

/** 将数据库中的唯一策略事实源同步到网关 Redis 快照。 */
@Component
public class GatewayContentPolicyPublisher {

    public static final String SNAPSHOT_KEY = "aipaas:gateway:content-policies";
    private static final Logger log = LoggerFactory.getLogger(GatewayContentPolicyPublisher.class);

    private final ContentSecurityRepository repository;
    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;

    public GatewayContentPolicyPublisher(ContentSecurityRepository repository, StringRedisTemplate redis,
                                         ObjectMapper objectMapper) {
        this.repository = repository;
        this.redis = redis;
        this.objectMapper = objectMapper;
    }

    public void invalidate() {
        redis.delete(SNAPSHOT_KEY);
    }

    public void publishAfterCommit() {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    publishSnapshot();
                }
            });
        } else {
            publishSnapshot();
        }
    }

    public void publishSnapshot() {
        try {
            List<RuleRecord> rules = repository.policies().stream()
                    .flatMap(policy -> repository.rules(policy.ruleType(), false).stream()).toList();
            redis.opsForValue().set(SNAPSHOT_KEY,
                    objectMapper.writeValueAsString(new Snapshot(repository.policies(), rules)));
        } catch (com.fasterxml.jackson.core.JsonProcessingException ex) {
            throw new IllegalStateException("Unable to serialize gateway content policies", ex);
        }
    }

    @EventListener(ApplicationReadyEvent.class)
    public void onReady() {
        syncSafely();
    }

    @Scheduled(fixedDelayString = "${aipaas.gateway-state.sync-interval-ms:60000}",
            initialDelayString = "${aipaas.gateway-state.sync-interval-ms:60000}")
    public void syncSafely() {
        try {
            publishSnapshot();
        } catch (RuntimeException ex) {
            log.error("网关内容策略快照同步失败，网关将保持 fail-closed", ex);
        }
    }
}
