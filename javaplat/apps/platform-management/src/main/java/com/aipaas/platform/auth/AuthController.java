package com.aipaas.platform.auth;

import com.aipaas.platform.common.api.ApiEnvelope;
import com.aipaas.platform.common.web.TraceContext;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import org.springframework.web.bind.annotation.*;

import java.time.OffsetDateTime;
import java.util.Map;

@RestController
@RequestMapping("/api/v1")
public class AuthController {

    private final AuthService auth;

    public AuthController(AuthService auth) {
        this.auth = auth;
    }

    @PostMapping("/auth/login")
    public ApiEnvelope<Map<String, Object>> login(@Valid @RequestBody LoginRequest request, HttpServletRequest http) {
        return ApiEnvelope.ok(auth.login(request.username(), request.password(), request.tenantId(), clientIp(http),
                http.getHeader("User-Agent"), TraceContext.current()), TraceContext.current());
    }

    @PostMapping("/auth/refresh")
    public ApiEnvelope<Map<String, Object>> refresh(@Valid @RequestBody RefreshRequest request, HttpServletRequest http) {
        return ApiEnvelope.ok(auth.refresh(request.refreshToken(), clientIp(http), http.getHeader("User-Agent"),
                TraceContext.current()), TraceContext.current());
    }

    @PostMapping("/auth/logout")
    public ApiEnvelope<Map<String, Object>> logout(HttpServletRequest http) {
        auth.logout(SecurityPrincipal.current(), (String) http.getAttribute("security.jti"),
                (OffsetDateTime) http.getAttribute("security.expiresAt"), clientIp(http), http.getHeader("User-Agent"),
                TraceContext.current());
        return ApiEnvelope.ok(Map.of("ok", true), TraceContext.current());
    }

    @GetMapping("/auth/me")
    public ApiEnvelope<Map<String, Object>> me() {
        return ApiEnvelope.ok(SecurityPrincipal.current().toCurrentUser(), TraceContext.current());
    }

    private static String clientIp(HttpServletRequest request) {
        String forwarded = request.getHeader("X-Forwarded-For");
        return forwarded == null || forwarded.isBlank() ? request.getRemoteAddr() : forwarded.split(",")[0].trim();
    }

    public record LoginRequest(@Size(min = 3, max = 64) @NotBlank String username,
                               @Size(min = 8, max = 128) @NotBlank String password, String tenantId) {
    }

    public record RefreshRequest(@Size(min = 32, max = 256) @NotBlank String refreshToken) {
    }
}
