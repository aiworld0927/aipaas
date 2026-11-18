package com.aipaas.platform.auth;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.aipaas.platform.crypto.KeyringService;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

@Service
public class JwtService {

    private static final Base64.Encoder ENCODER = Base64.getUrlEncoder().withoutPadding();
    private static final Base64.Decoder DECODER = Base64.getUrlDecoder();
    private final ObjectMapper objectMapper;
    private final KeyringService keyring;
    private final Duration accessTtl;

    public JwtService(ObjectMapper objectMapper, KeyringService keyring,
                      @Value("${aipaas.security.access-token-ttl:PT30M}") Duration accessTtl) {
        this.objectMapper = objectMapper;
        this.keyring = keyring;
        this.accessTtl = accessTtl;
    }

    public IssuedAccessToken issue(UserAccount user, String sessionId, Set<String> roles, Set<String> permissions) {
        try {
            Instant now = Instant.now();
            Instant expires = now.plus(accessTtl);
            String jti = UUID.randomUUID().toString();
            String header = ENCODER.encodeToString(objectMapper.writeValueAsBytes(Map.of("alg", "HS256", "typ", "JWT")));
            Map<String, Object> claims = new LinkedHashMap<>();
            claims.put("iss", "aipaas-p0");
            claims.put("sub", user.id());
            claims.put("tenantId", user.tenantId());
            claims.put("username", user.username());
            claims.put("sessionId", sessionId);
            claims.put("roles", roles.stream().sorted().toList());
            claims.put("permissions", permissions.stream().sorted().toList());
            claims.put("authVersion", user.authVersion());
            claims.put("jti", jti);
            claims.put("iat", now.getEpochSecond());
            claims.put("exp", expires.getEpochSecond());
            claims.put("type", "access");
            String payload = ENCODER.encodeToString(objectMapper.writeValueAsBytes(claims));
            String signature = sign(header + "." + payload);
            return new IssuedAccessToken(header + "." + payload + "." + signature, accessTtl.toSeconds(), jti,
                    OffsetDateTime.ofInstant(expires, ZoneOffset.UTC));
        } catch (GeneralSecurityException | java.io.IOException ex) {
            throw new IllegalStateException("Unable to issue access token", ex);
        }
    }

    public AccessTokenClaims verify(String token) {
        try {
            String[] parts = token.split("\\.");
            if (parts.length != 3) throw invalid();
            JsonNode header = objectMapper.readTree(DECODER.decode(parts[0]));
            if (!"HS256".equals(header.path("alg").asText()) || !"JWT".equals(header.path("typ").asText())) throw invalid();
            String expected = sign(parts[0] + "." + parts[1]);
            if (!MessageDigest.isEqual(expected.getBytes(StandardCharsets.US_ASCII),
                    parts[2].getBytes(StandardCharsets.US_ASCII))) throw invalid();
            JsonNode claims = objectMapper.readTree(DECODER.decode(parts[1]));
            if (!"aipaas-p0".equals(claims.path("iss").asText()) || !"access".equals(claims.path("type").asText())
                    || claims.path("sub").asText().isBlank() || claims.path("jti").asText().isBlank()) throw invalid();
            Instant expires = Instant.ofEpochSecond(claims.path("exp").asLong(0));
            if (!expires.isAfter(Instant.now())) {
                throw new ApiException(ErrorCodes.AUTH_TOKEN_EXPIRED, HttpStatus.UNAUTHORIZED, "访问令牌已过期");
            }
            return new AccessTokenClaims(claims.path("sub").asText(), claims.path("tenantId").asText("default"),
                    claims.path("sessionId").asText(), claims.path("jti").asText(), claims.path("authVersion").asLong(),
                    OffsetDateTime.ofInstant(expires, ZoneOffset.UTC));
        } catch (ApiException ex) {
            throw ex;
        } catch (Exception ex) {
            throw invalid();
        }
    }

    private String sign(String value) throws GeneralSecurityException {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(keyring.jwtSecret(), "HmacSHA256"));
        return ENCODER.encodeToString(mac.doFinal(value.getBytes(StandardCharsets.US_ASCII)));
    }

    private static ApiException invalid() {
        return new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED, HttpStatus.UNAUTHORIZED, "访问令牌无效");
    }

    public record IssuedAccessToken(String token, long expiresIn, String jti, OffsetDateTime expiresAt) {
    }

    public record AccessTokenClaims(String userId, String tenantId, String sessionId, String jti,
                                    long authVersion, OffsetDateTime expiresAt) {
    }
}
