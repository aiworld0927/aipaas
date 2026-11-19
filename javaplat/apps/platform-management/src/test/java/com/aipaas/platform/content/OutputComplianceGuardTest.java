package com.aipaas.platform.content;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.content.ContentSecurityEngine.CheckResult;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** #157 输出合规：合规不误拦、BLOCK 拦截、REPLACE 替换、SSE 跨 chunk 不绕过。 */
class OutputComplianceGuardTest {

    private ContentSecurityService security;
    private OutputComplianceGuard guard;

    @BeforeEach
    void setUp() {
        security = mock(ContentSecurityService.class);
        guard = new OutputComplianceGuard(security, new ObjectMapper());
        when(security.checkOutput(anyString())).thenAnswer(invocation -> {
            String text = invocation.getArgument(0);
            String compact = text.replace("\n", "");
            if (compact.contains("违禁") || compact.contains("恐怖主义")) {
                return new CheckResult(ContentSecurityService.OUTPUT, false, "BLOCK",
                        List.of("VIOLENCE"), 1, null, "v1", "tr_test");
            }
            if (compact.contains("敏感")) {
                return new CheckResult(ContentSecurityService.OUTPUT, true, "REPLACE",
                        List.of("SENSITIVE"), 1, compact.replace("敏感", "[已净化]"), "v1", "tr_test");
            }
            return new CheckResult(ContentSecurityService.OUTPUT, true, "ALLOW",
                    List.of(), 0, null, "v1", "tr_test");
        });
    }

    @Test
    void benignBodyPassesThroughUnchanged() {
        String body = "{\"code\":\"OK\",\"data\":{\"content\":\"本文件适用于金融数据安全\",\"answerId\":\"a1\"}}";
        assertEquals(body, guard.sanitizeJsonBody(body));
    }

    @Test
    void blockedBodyIsRejectedWithAuditableCode() {
        String body = "{\"code\":\"OK\",\"data\":{\"content\":\"这是违禁内容\"}}";
        ApiException ex = assertThrows(ApiException.class, () -> guard.sanitizeJsonBody(body));
        assertEquals(ErrorCodes.BIZ_CONTENT_BLOCKED, ex.getErrorCode());
        assertEquals(422, ex.getStatus().value());
    }

    @Test
    void replaceRewritesOnlyContentAndPreservesOtherFields() throws Exception {
        String body = "{\"code\":\"OK\",\"data\":{\"content\":\"这是敏感内容 ABC\",\"answerId\":\"a1\"}}";
        String output = guard.sanitizeJsonBody(body);
        var node = new ObjectMapper().readTree(output);
        assertEquals("这是[已净化]内容 ABC", node.path("data").path("content").asText());
        assertEquals("a1", node.path("data").path("answerId").asText());
    }

    @Test
    void sseBlockStopsStreamingAndNeverForwardsViolation() throws IOException {
        String sse = """
                event: meta
                data: {"answerId":"a1"}

                event: delta
                data: {"content":"这是违禁内容"}

                event: done
                data: {"answerId":"a1","content":"这是违禁内容"}

                """;
        String output = relay(sse);
        assertTrue(output.contains("event: error"), output);
        assertTrue(output.contains(ErrorCodes.BIZ_CONTENT_BLOCKED), output);
        assertFalse(output.contains("违禁"), output);
    }

    @Test
    void sseCrossChunkPatternIsStillBlocked() throws IOException {
        String sse = """
                event: delta
                data: {"content":"这是违"}

                event: delta
                data: {"content":"禁内容"}

                event: done
                data: {"content":""}

                """;
        String output = relay(sse);
        assertTrue(output.contains("event: error"), output);
        assertFalse(output.contains("违禁"), output);
    }

    @Test
    void sseReplaceSanitizesDelta() throws IOException {
        String sse = """
                event: delta
                data: {"content":"敏感内容"}

                event: done
                data: {"content":"敏感内容"}

                """;
        String output = relay(sse);
        assertTrue(output.contains("[已净化]内容"), output);
        assertFalse(output.contains("敏感"), output);
        assertFalse(output.contains("event: error"), output);
    }

    @Test
    void benignSsePassesThrough() throws IOException {
        String sse = """
                event: meta
                data: {"answerId":"a1"}

                event: delta
                data: {"content":"合规回答"}

                event: done
                data: {"answerId":"a1","content":"合规回答"}

                """;
        String output = relay(sse);
        assertEquals(sse, output);
    }

    private String relay(String sse) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        boolean blocked = guard.relay(
                new ByteArrayInputStream(sse.getBytes(StandardCharsets.UTF_8)), output, "tr_test");
        assertFalse(blocked && output.size() == 0);
        return output.toString(StandardCharsets.UTF_8);
    }
}
