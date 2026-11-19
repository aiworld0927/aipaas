package com.aipaas.platform.config;

import io.micrometer.core.instrument.MeterRegistry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

/** 安全告警只记录元数据；调用方禁止传入口令、密钥、令牌或输入原文。 */
@Service
public class SecurityAlertService {

    private static final Logger log = LoggerFactory.getLogger("SECURITY_ALERT");
    private final MeterRegistry meterRegistry;

    public SecurityAlertService(MeterRegistry meterRegistry) {
        this.meterRegistry = meterRegistry;
    }

    public void alert(String type, String objectType, String objectId, String traceId, String safeSummary) {
        meterRegistry.counter("aipaas.security.alerts", "type", type).increment();
        log.warn("type={} objectType={} objectId={} traceId={} summary={}",
                type, objectType, objectId, traceId, safeSummary);
    }
}
