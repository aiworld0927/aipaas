package com.aipaas.platform.ingest;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

/** 事件驱动的任务分发：上传提交后异步执行，调用方线程不被阻塞。 */
@Component
public class IngestTaskDispatcher {

    private static final Logger log = LoggerFactory.getLogger(IngestTaskDispatcher.class);

    private final IngestTaskRunner runner;

    public IngestTaskDispatcher(IngestTaskRunner runner) {
        this.runner = runner;
    }

    @Async("ingestExecutor")
    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT, fallbackExecution = true)
    public void onQueued(IngestTaskQueuedEvent event) {
        dispatchNow(event.taskId());
    }

    @Async("ingestExecutor")
    public void dispatch(String taskId) {
        dispatchNow(taskId);
    }

    private void dispatchNow(String taskId) {
        try {
            runner.run(taskId);
        } catch (Exception ex) {
            // 执行器内部已按上限重试并落库；此处兜底记录，避免线程池静默吞异常。
            log.error("接入任务异步执行未捕获异常 taskId={}", taskId, ex);
        }
    }
}
