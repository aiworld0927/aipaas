package com.aipaas.platform.content;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.content.ContentSecurityService.CheckResult;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * #157 输出合规守卫：
 * - 非流式响应：检查 ``data.content`` / ``data.answer``，BLOCK 抛 422，REPLACE 原位替换；
 * - 流式 SSE：逐事件检查 delta/done，REPLACE 改写内容，BLOCK 输出 error 事件并终止，
 *   阻断后不再转发任何上游内容。
 *
 * 合规内容（无规则命中）原样透传，不做改写。
 */
@Component
public class OutputComplianceGuard {

    private static final int MAX_CHECK_CHARS = 20_000;

    private final ContentSecurityService security;
    private final ObjectMapper mapper;

    public OutputComplianceGuard(ContentSecurityService security, ObjectMapper mapper) {
        this.security = security;
        this.mapper = mapper;
    }

    /** 非流式 JSON 响应检查；返回可能被替换后的响应体。 */
    public String sanitizeJsonBody(String body) {
        if (body == null || body.isBlank()) return body;
        JsonNode root;
        try {
            root = mapper.readTree(body);
        } catch (IOException ex) {
            return body;
        }
        if (root == null || !root.isObject()) return body;
        TextSlot slot = findSlot(root);
        if (slot == null || slot.text().isBlank()) return body;
        CheckResult result = inspect(slot.text());
        if (result == null) return body;
        if (blocked(result)) {
            throw blockedException();
        }
        if (replaced(result)) {
            slot.parent().put(slot.field(), result.sanitizedContent());
            try {
                return mapper.writeValueAsString(root);
            } catch (IOException ex) {
                throw blockedException();
            }
        }
        return body;
    }

    /** SSE 转发；返回 true 表示命中 BLOCK 并已输出 error 事件。 */
    public boolean relay(InputStream input, OutputStream output, String traceId) throws IOException {
        BufferedReader reader = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8));
        Writer writer = new OutputStreamWriter(output, StandardCharsets.UTF_8);
        List<String> lines = new ArrayList<>();
        SseEvent pending = null;
        String pendingText = null;
        try {
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isEmpty()) {
                    SseEvent event = SseEvent.parse(lines);
                    lines.clear();
                    if (event == null) {
                        writer.write("\n");
                        writer.flush();
                        continue;
                    }
                    if (isDelta(event)) {
                        String text = contentOf(event.data());
                        if (text == null || text.isBlank()) {
                            flush(writer, pending);
                            pending = null;
                            pendingText = null;
                            writeEvent(writer, event);
                            continue;
                        }
                        // 跨 chunk 命中：把上一块与本块合并检查，避免规则被切分绕过。
                        if (pendingText != null) {
                            CheckResult cross = inspect(pendingText + "\n" + text);
                            if (cross != null && blocked(cross)) {
                                writeBlocked(writer, traceId);
                                return true;
                            }
                        }
                        CheckResult result = inspect(text);
                        if (result != null && blocked(result)) {
                            writeBlocked(writer, traceId);
                            return true;
                        }
                        if (result != null && replaced(result)) {
                            event = withContent(event, result.sanitizedContent());
                            text = result.sanitizedContent();
                        }
                        // 延迟一个事件写出，确保相邻 chunk 拼接后的规则也能被拦截。
                        flush(writer, pending);
                        pending = event;
                        pendingText = text;
                        continue;
                    }
                    flush(writer, pending);
                    pending = null;
                    pendingText = null;
                    if (isDone(event)) {
                        CheckResult result = inspect(contentOf(event.data()));
                        if (result != null && blocked(result)) {
                            writeBlocked(writer, traceId);
                            return true;
                        }
                        if (result != null && replaced(result)) {
                            event = withContent(event, result.sanitizedContent());
                        }
                    }
                    writeEvent(writer, event);
                    continue;
                }
                lines.add(line);
            }
            if (!lines.isEmpty()) {
                SseEvent event = SseEvent.parse(lines);
                if (event != null) {
                    flush(writer, pending);
                    pending = null;
                    if (isDone(event)) {
                        CheckResult result = inspect(contentOf(event.data()));
                        if (result != null && blocked(result)) {
                            writeBlocked(writer, traceId);
                            return true;
                        }
                        if (result != null && replaced(result)) {
                            event = withContent(event, result.sanitizedContent());
                        }
                    }
                    writeEvent(writer, event);
                }
            }
            flush(writer, pending);
            writer.flush();
            return false;
        } finally {
            writer.flush();
        }
    }

    private CheckResult inspect(String text) {
        if (text == null || text.isBlank()) return null;
        String candidate = text.length() <= MAX_CHECK_CHARS ? text : text.substring(0, MAX_CHECK_CHARS);
        return security.checkOutput(candidate);
    }

    private static boolean blocked(CheckResult result) {
        return result != null && (!result.passed() || "BLOCK".equals(result.action()));
    }

    private static boolean replaced(CheckResult result) {
        return result != null && result.passed() && "REPLACE".equals(result.action())
                && result.sanitizedContent() != null;
    }

    private static ApiException blockedException() {
        return new ApiException(ErrorCodes.BIZ_CONTENT_BLOCKED, HttpStatus.UNPROCESSABLE_ENTITY,
                "输出内容违反安全规则，已拦截");
    }

    private void writeBlocked(Writer writer, String traceId) throws IOException {
        String payload = mapper.writeValueAsString(Map.of(
                "code", ErrorCodes.BIZ_CONTENT_BLOCKED,
                "message", "输出内容违反安全规则，已拦截",
                "traceId", traceId == null ? "" : traceId));
        writer.write("event: error\n");
        writer.write("data: " + payload + "\n\n");
        writer.flush();
    }

    private static void flush(Writer writer, SseEvent event) throws IOException {
        if (event != null) writeEvent(writer, event);
    }

    private static void writeEvent(Writer writer, SseEvent event) throws IOException {
        writer.write(event.render());
        writer.flush();
    }

    private SseEvent withContent(SseEvent event, String sanitized) {
        try {
            JsonNode node = mapper.readTree(event.data());
            if (node instanceof ObjectNode object) {
                if (object.has("content")) object.put("content", sanitized);
                else if (object.has("answer")) object.put("answer", sanitized);
                else object.put("content", sanitized);
                return new SseEvent(event.event(), event.id(), mapper.writeValueAsString(object));
            }
        } catch (IOException ignored) {
            // 非 JSON data 不尝试改写，交由上层按原样输出（BLOCK 已在前面处理）。
        }
        return event;
    }

    private String contentOf(String data) {
        if (data == null || data.isBlank()) return null;
        try {
            JsonNode node = mapper.readTree(data);
            if (node.isTextual()) return node.asText();
            if (node.hasNonNull("content")) return node.get("content").asText();
            if (node.hasNonNull("answer")) return node.get("answer").asText();
        } catch (IOException ignored) {
            return data;
        }
        return null;
    }

    private static TextSlot findSlot(JsonNode root) {
        JsonNode data = root.path("data");
        TextSlot slot = slotIn(data);
        return slot != null ? slot : slotIn(root);
    }

    private static TextSlot slotIn(JsonNode node) {
        if (node == null || !node.isObject()) return null;
        for (String field : List.of("content", "answer")) {
            JsonNode value = node.get(field);
            if (value != null && value.isTextual()) {
                return new TextSlot((ObjectNode) node, field, value.asText());
            }
        }
        return null;
    }

    private static boolean isDelta(SseEvent event) {
        return "delta".equals(event.event());
    }

    private static boolean isDone(SseEvent event) {
        return "done".equals(event.event());
    }

    private record TextSlot(ObjectNode parent, String field, String text) {
    }

    private record SseEvent(String event, String id, String data) {
        static SseEvent parse(List<String> lines) {
            if (lines.isEmpty()) return null;
            String event = "message";
            String id = null;
            List<String> data = new ArrayList<>();
            for (String line : lines) {
                if (line.startsWith(":")) continue;
                int colon = line.indexOf(':');
                String field = colon < 0 ? line : line.substring(0, colon);
                String value = colon < 0 ? "" : line.substring(colon + 1);
                if (value.startsWith(" ")) value = value.substring(1);
                switch (field) {
                    case "event" -> event = value;
                    case "id" -> id = value;
                    case "data" -> data.add(value);
                    default -> {
                        // 未知字段原样忽略，避免破坏第三方 SSE 扩展。
                    }
                }
            }
            return new SseEvent(event, id, String.join("\n", data));
        }

        String render() {
            StringBuilder builder = new StringBuilder();
            if (id != null) builder.append("id: ").append(id).append('\n');
            if (!"message".equals(event)) builder.append("event: ").append(event).append('\n');
            for (String line : data.split("\n", -1)) {
                builder.append("data: ").append(line).append('\n');
            }
            builder.append('\n');
            return builder.toString();
        }
    }

    private static final ObjectMapper MAPPER_HOLDER = new ObjectMapper();
}
