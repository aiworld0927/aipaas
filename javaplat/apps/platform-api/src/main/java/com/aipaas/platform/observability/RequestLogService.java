package com.aipaas.platform.observability;

import com.aipaas.platform.common.api.PageResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

/** #173 请求日志写入、检索与保留期清理；写入失败不影响业务请求。 */
@Service
public class RequestLogService {

    private static final Logger log = LoggerFactory.getLogger(RequestLogService.class);

    private final RequestLogRepository repository;
    private final int retentionDays;
    private final boolean retentionEnabled;

    public RequestLogService(RequestLogRepository repository,
                             @Value("${aipaas.observability.request-log-retention-days:30}") int retentionDays,
                             @Value("${aipaas.observability.request-log-retention-enabled:true}")
                             boolean retentionEnabled) {
        this.repository = repository;
        this.retentionDays = Math.max(1, retentionDays);
        this.retentionEnabled = retentionEnabled;
    }

    public void record(RequestLogEntry entry) {
        try {
            repository.insert(entry);
        } catch (RuntimeException ex) {
            // 请求日志不可影响主流程，也不能把原始内容打进异常日志。
            log.warn("request log persistence failed traceId={} path={} error={}",
                    entry.traceId(), entry.path(), ex.getClass().getSimpleName());
        }
    }

    public PageResult<RequestLogEntry> search(RequestLogQuery query, int page, int pageSize) {
        long total = repository.count(query);
        long offset = (long) (page - 1) * pageSize;
        return PageResult.of(repository.search(query, pageSize, offset), total, page, pageSize);
    }

    @Scheduled(cron = "${aipaas.observability.request-log-retention-cron:0 15 3 * * *}")
    public void purgeExpired() {
        if (!retentionEnabled) return;
        int deleted = repository.deleteExpired(retentionDays);
        if (deleted > 0) {
            log.info("request log retention purge completed: expiredRows={} retentionDays={}",
                    deleted, retentionDays);
        }
    }
}
