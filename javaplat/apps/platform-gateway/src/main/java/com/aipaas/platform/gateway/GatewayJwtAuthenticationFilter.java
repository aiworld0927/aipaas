package com.aipaas.platform.gateway;

import com.aipaas.platform.auth.GatewayAuthSnapshot;
import com.aipaas.platform.auth.JwtTokenVerifier;
import com.aipaas.platform.auth.SecurityPrincipal;
import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.List;

/** 本地验签 + Redis 用户/撤销快照校验；不访问管理数据库。 */
@Component
public class GatewayJwtAuthenticationFilter extends OncePerRequestFilter {

    public static final String KB_IDS_ATTRIBUTE = "security.kbIds";
    private static final String USER_KEY_PREFIX = "aipaas:gateway:auth:";
    private static final String REVOKED_KEY_PREFIX = "aipaas:gateway:revoked:";

    private final StringRedisTemplate redis;
    private final ObjectMapper objectMapper;
    private final JwtTokenVerifier verifier;

    @Autowired
    public GatewayJwtAuthenticationFilter(StringRedisTemplate redis, ObjectMapper objectMapper,
                                          @Value("${aipaas.security.jwt-verify-secret-file:/var/lib/aipaas/jwt-verify/jwt-secret}")
                                          String secretFile) throws IOException {
        this(redis, objectMapper, readSecret(secretFile));
    }

    public GatewayJwtAuthenticationFilter(StringRedisTemplate redis, ObjectMapper objectMapper, byte[] secret) {
        this.redis = redis;
        this.objectMapper = objectMapper;
        this.verifier = new JwtTokenVerifier(objectMapper, secret);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path.endsWith("/auth/login") || path.endsWith("/auth/refresh")
                || path.endsWith("/auth/register") || !path.startsWith("/api/v1");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String header = request.getHeader("Authorization");
        if (!StringUtils.hasText(header)) {
            chain.doFilter(request, response);
            return;
        }
        try {
            if (!header.startsWith("Bearer ") || header.length() <= 7) {
                throw new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED, HttpStatus.UNAUTHORIZED, "Bearer 凭证格式无效");
            }
            JwtTokenVerifier.AccessTokenClaims claims = verifier.verify(header.substring(7).trim());
            if (Boolean.TRUE.equals(redis.hasKey(REVOKED_KEY_PREFIX + claims.jti()))) {
                throw new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED, HttpStatus.UNAUTHORIZED, "访问令牌已撤销");
            }
            String serialized = redis.opsForValue().get(USER_KEY_PREFIX + claims.userId());
            if (serialized == null) {
                throw new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED, HttpStatus.UNAUTHORIZED,
                        "账号不可用或令牌版本已失效");
            }
            GatewayAuthSnapshot snapshot = objectMapper.readValue(serialized, GatewayAuthSnapshot.class);
            if (!claims.tenantId().equals(snapshot.tenantId()) || claims.authVersion() != snapshot.authVersion()
                    || !"ACTIVE".equals(snapshot.status())) {
                throw new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED, HttpStatus.UNAUTHORIZED,
                        "账号不可用或令牌版本已失效");
            }
            SecurityPrincipal principal = snapshot.toPrincipal(claims.sessionId());
            SecurityContextHolder.getContext().setAuthentication(
                    new UsernamePasswordAuthenticationToken(principal, null, List.of()));
            request.setAttribute("security.jti", claims.jti());
            request.setAttribute("security.expiresAt", claims.expiresAt());
            request.setAttribute("security.userId", principal.userId());
            request.setAttribute("security.tenantId", principal.tenantId());
            request.setAttribute(KB_IDS_ATTRIBUTE, snapshot.kbIds());
        } catch (ApiException ex) {
            SecurityContextHolder.clearContext();
            writeError(response, ex.getStatus().value(), ex.getErrorCode(), ex.getReason(), objectMapper);
            return;
        } catch (Exception ex) {
            SecurityContextHolder.clearContext();
            writeError(response, HttpStatus.SERVICE_UNAVAILABLE.value(), ErrorCodes.SYS_DEPENDENCY_UNAVAILABLE,
                    "认证状态暂不可用", objectMapper);
            return;
        }
        chain.doFilter(request, response);
    }

    static void writeError(HttpServletResponse response, int status, String code, String message,
                           ObjectMapper objectMapper) throws IOException {
        response.setStatus(status);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setHeader(TraceContext.TRACE_HEADER, TraceContext.current());
        objectMapper.writeValue(response.getOutputStream(),
                ApiEnvelope.error(code, message, "authentication failed", TraceContext.current()));
    }

    private static byte[] readSecret(String path) throws IOException {
        String encoded = Files.readString(Path.of(path).toAbsolutePath().normalize(), StandardCharsets.UTF_8).trim();
        return Base64.getDecoder().decode(encoded);
    }
}
