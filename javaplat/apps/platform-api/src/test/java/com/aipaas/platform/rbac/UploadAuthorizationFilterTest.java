package com.aipaas.platform.rbac;

import com.aipaas.platform.audit.AuditService;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.config.SecurityAlertService;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class UploadAuthorizationFilterTest {

    private RbacService rbac;
    private AuditService audit;
    private SecurityAlertService alerts;
    private UploadAuthorizationFilter filter;
    private SecurityPrincipal principal;

    @BeforeEach
    void setUp() {
        rbac = mock(RbacService.class);
        audit = mock(AuditService.class);
        alerts = mock(SecurityAlertService.class);
        filter = new UploadAuthorizationFilter(rbac, audit, alerts, new ObjectMapper());
        principal = new SecurityPrincipal("usr_1", "default", "uploader", "Uploader", "session",
                Set.of("KB_UPLOADER"), Set.of("doc:upload"));
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken(principal, null, List.of()));
    }

    @AfterEach
    void tearDown() {
        SecurityContextHolder.clearContext();
    }

    @Test
    void deniedUploadNeverReachesMultipartControllerAndEmitsAuditAndAlert() throws Exception {
        MockHttpServletRequest request = uploadRequest("kb_other");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);
        when(rbac.allowed(principal, "doc:upload", "kb_other")).thenReturn(false);

        filter.doFilter(request, response, chain);

        assertEquals(403, response.getStatus());
        assertTrue(response.getContentAsString().contains("PERM.UPLOAD_FORBIDDEN"));
        verify(chain, never()).doFilter(any(), any());
        verify(audit).record(eq(principal), eq("permission.denied"), eq("knowledgeBase"), eq("kb_other"),
                eq("DENIED"), eq(Map.of("permission", "doc:upload")), eq("127.0.0.1"), eq("upload-test"), any());
        verify(alerts).alert(eq("UPLOAD_PERMISSION_DENIED"), eq("knowledgeBase"), eq("kb_other"), any(),
                eq("permission=doc:upload"));
    }

    @Test
    void everyDeniedTargetIsBlockedWithoutCallingDownstream() throws Exception {
        FilterChain chain = mock(FilterChain.class);
        when(rbac.allowed(eq(principal), eq("doc:upload"), anyString())).thenReturn(false);

        for (String kbId : List.of("kb_other", "kb_missing", "kb_private", "kb_finance", "kb_hr")) {
            MockHttpServletResponse response = new MockHttpServletResponse();
            filter.doFilter(uploadRequest(kbId), response, chain);
            assertEquals(403, response.getStatus(), kbId);
        }

        verify(chain, never()).doFilter(any(), any());
        verify(audit, times(5)).record(eq(principal), eq("permission.denied"), eq("knowledgeBase"), anyString(),
                eq("DENIED"), anyMap(), anyString(), anyString(), any());
        verify(alerts, times(5)).alert(eq("UPLOAD_PERMISSION_DENIED"), eq("knowledgeBase"), anyString(), any(), anyString());
    }

    @Test
    void anonymousUploadGets401WithoutPermissionAlert() throws Exception {
        SecurityContextHolder.clearContext();
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);

        filter.doFilter(uploadRequest("kb_default"), response, chain);

        assertEquals(401, response.getStatus());
        assertTrue(response.getContentAsString().contains("AUTH.UNAUTHENTICATED"));
        verify(chain, never()).doFilter(any(), any());
        verifyNoInteractions(rbac, alerts);
        verify(audit, never()).record(any(), eq("permission.denied"), anyString(), anyString(), anyString(), anyMap(),
                any(), any(), any());
    }

    @Test
    void grantedUploadProceedsWithoutDeniedAudit() throws Exception {
        MockHttpServletRequest request = uploadRequest("kb_default");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain chain = mock(FilterChain.class);
        when(rbac.allowed(principal, "doc:upload", "kb_default")).thenReturn(true);

        filter.doFilter(request, response, chain);

        verify(chain).doFilter(request, response);
        verify(audit, never()).record(any(), eq("permission.denied"), anyString(), anyString(), anyString(), anyMap(),
                any(), any(), any());
        verify(alerts, never()).alert(anyString(), anyString(), anyString(), any(), anyString());
    }

    private static MockHttpServletRequest uploadRequest(String kbId) {
        MockHttpServletRequest request = new MockHttpServletRequest("POST",
                "/api/v1/knowledge-bases/" + kbId + "/documents");
        request.setRemoteAddr("127.0.0.1");
        request.addHeader("User-Agent", "upload-test");
        return request;
    }
}
