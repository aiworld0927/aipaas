package com.aipaas.platform.ingest;

/** 任务入队事件：上传事务提交后触发异步解析。 */
public record IngestTaskQueuedEvent(String taskId) {
}
