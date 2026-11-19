package com.aipaas.platform.ingest;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/** 任务状态推送：按 taskId 维护 SSE 订阅者，支持 meta/status/done/error/ping。 */
@Component
public class IngestEventPublisher {

    private static final Logger log = LoggerFactory.getLogger(IngestEventPublisher.class);
    private static final long TIMEOUT_MILLIS = 30L * 60L * 1000L;

    private final Map<String, Set<SseEmitter>> subscribers = new ConcurrentHashMap<>();

    public SseEmitter subscribe(String taskId, Map<String, Object> snapshot) {
        SseEmitter emitter = new SseEmitter(TIMEOUT_MILLIS);
        subscribers.computeIfAbsent(taskId, key -> ConcurrentHashMap.newKeySet()).add(emitter);
        emitter.onCompletion(() -> remove(taskId, emitter));
        emitter.onTimeout(() -> remove(taskId, emitter));
        emitter.onError(error -> remove(taskId, emitter));
        send(emitter, taskId, "meta", snapshot);
        return emitter;
    }

    public void publish(String taskId, String event, Map<String, Object> data) {
        for (SseEmitter emitter : emitters(taskId)) {
            send(emitter, taskId, event, data);
        }
    }

    /** 推送终态事件后关闭连接。 */
    public void complete(String taskId, String event, Map<String, Object> data) {
        for (SseEmitter emitter : emitters(taskId)) {
            send(emitter, taskId, event, data);
            try {
                emitter.complete();
            } catch (RuntimeException ex) {
                log.debug("SSE complete skipped taskId={}", taskId);
            }
            remove(taskId, emitter);
        }
    }

    @Scheduled(fixedRate = 15_000)
    public void heartbeat() {
        Map<String, Object> ping = Map.of("at", OffsetDateTime.now(ZoneOffset.UTC));
        subscribers.forEach((taskId, emitters) -> {
            for (SseEmitter emitter : emitters) {
                send(emitter, taskId, "ping", ping);
            }
        });
    }

    public int subscriberCount(String taskId) {
        Set<SseEmitter> emitters = subscribers.get(taskId);
        return emitters == null ? 0 : emitters.size();
    }

    private Set<SseEmitter> emitters(String taskId) {
        Set<SseEmitter> emitters = subscribers.get(taskId);
        return emitters == null ? Set.of() : Set.copyOf(emitters);
    }

    private void send(SseEmitter emitter, String taskId, String event, Map<String, Object> data) {
        try {
            emitter.send(SseEmitter.event().name(event).data(data, MediaType.APPLICATION_JSON));
        } catch (IOException | IllegalStateException ex) {
            log.warn("SSE 推送失败并移除订阅 taskId={} event={}: {}", taskId, event, ex.getMessage());
            remove(taskId, emitter);
        }
    }

    private void remove(String taskId, SseEmitter emitter) {
        Set<SseEmitter> emitters = subscribers.get(taskId);
        if (emitters == null) {
            return;
        }
        emitters.remove(emitter);
        if (emitters.isEmpty()) {
            subscribers.remove(taskId, emitters);
        }
    }
}
