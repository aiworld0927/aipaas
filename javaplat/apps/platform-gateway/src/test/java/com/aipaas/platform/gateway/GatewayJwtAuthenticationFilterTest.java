package com.aipaas.platform.gateway;

import com.aipaas.platform.auth.GatewayAuthSnapshot;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.RedisConnectionFailureException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.core.context.SecurityContextHolder;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** 网关本地验签与 Redis 快照校验：不访问管理数据库，缺失或异常时 fail-closed。 */
class GatewayJwtAuthenticationFilterTest {

    private static final byte[] SECRET = "0123456789abcdef0123456789abcdef".getBytes(StandardCharsets.UTF_8);
    private static final String USER_KEY = "aipaas:gateway:auth:usr_1";
    private static final String REVOKED_KEY = "aipaas:gateway:revoked:jti-1";

    private final ObjectMapper objectMapper = new ObjectMapper();
    private StringRedisTemplate redis;
    private ValueOperations<String, String> values;
    private GatewayJwtAuthenticationFilter filter;

    @BeforeEach
    @SuppressWarnings("unchecked")
    void setUp() {
        redis = mock(StringRedisTemplate.class);
        values = mock(ValueOperations.class);
        when(redis.opsForValue()).thenReturn(values);
        when(redis.hasKey(anyString())).thenReturn(false);
        filter = new GatewayJwtAuthenticationFilter(redis, objectMapper, SECRET);
    }

    @AfterEach
    void tearDown() {
        SecurityContextHolder.clearContext();
    }

    @Test
    void validTokenUsesSnapshotAndIgnoresSpoofedIdentityHeaders() throws Exception {
        when(values.get(USER_KEY)).thenReturn(objectMapper.writeValueAsString(snapshot("ACTIVE", 7)));
        MockHttpServletRequest request = apiRequest("/api/v1/chat");
        request.addHeader("Authorization", "Bearer " + token("usr_1", "default", 7, "jti-1"));
        request.addHeader("X-User-Id", "attacker");
        request.addHeader("X-Permissions", "*");
        MockHttpServletResponse response = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(request, response, chain);

        SecurityPrincipal principal = SecurityPrincipal.current();
        assertEquals("usr_1", principal.userId());
        assertEquals("default", principal.tenantId());
        assertEquals(Set.of("analyst"), principal.roles());
        assertEquals(Set.of("chat:use"), principal.permissions());
        assertEquals("session-1", principal.sessionId());
        assertEquals(List.of("kb-1", "kb-2"), request.getAttribute(GatewayJwtAuthenticationFilter.KB_IDS_ATTRIBUTE));
        assertEquals("jti-1", request.getAttribute("security.jti"));
        assertNotNull(chain.getRequest(), "有效令牌必须继续进入后续链路");
        assertEquals(200, response.getStatus());
    }

    @Test
    void revokedTokenIsRejected() throws Exception {
        when(redis.hasKey(REVOKED_KEY)).thenReturn(true);
        when(values.get(USER_KEY)).thenReturn(objectMapper.writeValueAsString(snapshot("ACTIVE", 7)));
        MockHttpServletRequest request = apiRequest("/api/v1/chat");
        request.addHeader("Authorization", "Bearer " + token("usr_1", "default", 7, "jti-1"));
        MockHttpServletResponse response = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(request, response, chain);

        assertEquals(401, response.getStatus());
        assertEquals("AUTH.UNAUTHENTICATED", errorCode(response));
        assertNull(chain.getRequest());
        assertNull(SecurityContextHolder.getContext().getAuthentication());
    }

    @Test
    void missingSnapshotFailsClosed() throws Exception {
        when(values.get(USER_KEY)).thenReturn(null);
        MockHttpServletRequest request = apiRequest("/api/v1/knowledge-bases");
        request.addHeader("Authorization", "Bearer " + token("usr_1", "default", 7, "jti-1"));
        MockHttpServletResponse response = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(request, response, chain);

        assertEquals(401, response.getStatus());
        assertNull(chain.getRequest());
    }

    @Test
    void staleAuthVersionOrInactiveStatusIsRejected() throws Exception {
        when(values.get(USER_KEY)).thenReturn(objectMapper.writeValueAsString(snapshot("LOCKED", 7)));
        MockHttpServletRequest request = apiRequest("/api/v1/knowledge-bases");
        request.addHeader("Authorization", "Bearer " + token("usr_1", "default", 7, "jti-1"));
        MockHttpServletResponse response = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(request, response, chain);

        assertEquals(401, response.getStatus());
        assertNull(chain.getRequest());
    }

    @Test
    void redisFailureReturnsServiceUnavailableInsteadOfAllowing() throws Exception {
        when(redis.hasKey(anyString())).thenThrow(new RedisConnectionFailureException("redis down"));
        MockHttpServletRequest request = apiRequest("/api/v1/knowledge-bases");
        request.addHeader("Authorization", "Bearer " + token("usr_1", "default", 7, "jti-1"));
        MockHttpServletResponse response = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(request, response, chain);

        assertEquals(503, response.getStatus());
        assertEquals("SYS.DEPENDENCY_UNAVAILABLE", errorCode(response));
        assertNull(chain.getRequest());
    }

    @Test
    void requestWithoutAuthorizationDefersToSecurityChain() throws Exception {
        MockHttpServletRequest request = apiRequest("/api/v1/knowledge-bases");
        MockHttpServletResponse response = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(request, response, chain);

        assertNotNull(chain.getRequest());
        assertNull(SecurityContextHolder.getContext().getAuthentication());
    }

    @Test
    void nonApiPathsAreNotFiltered() throws Exception {
        MockHttpServletRequest request = apiRequest("/healthz");
        MockHttpServletResponse response = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(request, response, chain);

        assertNotNull(chain.getRequest());
    }

    @Test
    void malformedBearerIsRejected() throws Exception {
        MockHttpServletRequest request = apiRequest("/api/v1/chat");
        request.addHeader("Authorization", "Basic abc");
        MockHttpServletResponse response = new MockHttpServletResponse();
        MockFilterChain chain = new MockFilterChain();

        filter.doFilter(request, response, chain);

        assertEquals(401, response.getStatus());
        assertNull(chain.getRequest());
        assertFalse(response.getContentAsString().isBlank());
    }

    private static MockHttpServletRequest apiRequest(String uri) {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", uri);
        request.setRequestURI(uri);
        return request;
    }

    private static GatewayAuthSnapshot snapshot(String status, long authVersion) {
        return new GatewayAuthSnapshot("usr_1", "default", "analyst", "Analyst", "session-1", status, authVersion,
                Set.of("analyst"), Set.of("chat:use"), List.of("kb-1", "kb-2"));
    }

    private String errorCode(MockHttpServletResponse response) throws Exception {
        JsonNode body = objectMapper.readTree(response.getContentAsString());
        return body.path("code").asText();
    }

    static String token(String userId, String tenantId, long authVersion, String jti) throws Exception {
        Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();
        String header = encoder.encodeToString(
                "{\"alg\":\"HS256\",\"typ\":\"JWT\"}".getBytes(StandardCharsets.UTF_8));
        String claims = encoder.encodeToString(("{\"iss\":\"aipaas-p0\",\"type\":\"access\",\"sub\":\"" + userId
                + "\",\"tenantId\":\"" + tenantId + "\",\"sessionId\":\"session-1\",\"jti\":\"" + jti
                + "\",\"authVersion\":" + authVersion + ",\"exp\":" + Instant.now().plusSeconds(600).getEpochSecond()
                + "}").getBytes(StandardCharsets.UTF_8));
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET, "HmacSHA256"));
        String signature = encoder.encodeToString(mac.doFinal((header + "." + claims).getBytes(StandardCharsets.US_ASCII)));
        return header + "." + claims + "." + signature;
    }
}
