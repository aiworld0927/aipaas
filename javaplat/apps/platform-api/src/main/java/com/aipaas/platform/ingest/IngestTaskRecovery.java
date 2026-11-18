package com.aipaas.platform.ingest;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

/** 启动补偿：进程重启后把遗留的 QUEUED 任务重新分发给线程池。 */
@Component
public class IngestTaskRecovery {

    private static final Logger log = LoggerFactory.getLogger(IngestTaskRecovery.class);

    private final IngestTaskRepository repository;
    private final IngestTaskDispatcher dispatcher;

    public IngestTaskRecovery(IngestTaskRepository repository, IngestTaskDispatcher dispatcher) {
        this.repository = repository;
        this.dispatcher = dispatcher;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void recover() {
        for (String taskId : repository.queuedTaskIds(200)) {
            log.info("补偿分发遗留任务 taskId={}", taskId);
            dispatcher.dispatch(taskId);
        }
    }
}
