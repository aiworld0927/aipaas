package com.aipaas.platform.auth;

import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.common.web.TraceContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;

@Component
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private final JwtService jwt;
    private final UserRepository users;
    private final AuthService auth;
    private final ObjectMapper objectMapper;

    public JwtAuthenticationFilter(JwtService jwt, UserRepository users, AuthService auth, ObjectMapper objectMapper) {
        this.jwt = jwt;
        this.users = users;
        this.auth = auth;
        this.objectMapper = objectMapper;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return path.endsWith("/auth/login") || path.endsWith("/auth/refresh") || !path.startsWith("/api/v1");
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
                throw new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED, org.springframework.http.HttpStatus.UNAUTHORIZED,
                        "Bearer 凭证格式无效");
            }
            JwtService.AccessTokenClaims claims = jwt.verify(header.substring(7).trim());
            if (users.isAccessTokenRevoked(claims.jti())) {
                throw new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED, org.springframework.http.HttpStatus.UNAUTHORIZED,
                        "访问令牌已撤销");
            }
            UserAccount user = users.findById(claims.userId())
                    .filter(u -> u.tenantId().equals(claims.tenantId()) && u.authVersion() == claims.authVersion())
                    .filter(u -> "ACTIVE".equals(u.status()))
                    .orElseThrow(() -> new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED,
                            org.springframework.http.HttpStatus.UNAUTHORIZED, "账号不可用或令牌版本已失效"));
            SecurityPrincipal principal = auth.principal(user, claims.sessionId());
            UsernamePasswordAuthenticationToken authentication = new UsernamePasswordAuthenticationToken(
                    principal, null, List.of());
            SecurityContextHolder.getContext().setAuthentication(authentication);
            request.setAttribute("security.jti", claims.jti());
            request.setAttribute("security.expiresAt", claims.expiresAt());
            // 供请求日志在 SecurityContext 清理后仍能取得操作者（#173）。
            request.setAttribute("security.userId", principal.userId());
            request.setAttribute("security.tenantId", principal.tenantId());
            chain.doFilter(request, response);
        } catch (ApiException ex) {
            SecurityContextHolder.clearContext();
            response.setStatus(ex.getStatus().value());
            response.setCharacterEncoding(StandardCharsets.UTF_8.name());
            response.setContentType(MediaType.APPLICATION_JSON_VALUE);
            response.setHeader(TraceContext.TRACE_HEADER, TraceContext.current());
            objectMapper.writeValue(response.getOutputStream(), ApiEnvelope.error(ex.getErrorCode(), ex.getReason(),
                    "authentication failed", TraceContext.current()));
        }
    }
}
