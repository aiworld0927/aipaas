package com.aipaas.platform.auth;

import com.aipaas.platform.common.api.ApiException;
import com.aipaas.platform.common.api.ErrorCodes;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Base64;

/** 仅供网关使用的本地 HS256 验签器，不依赖用户、权限或撤销表。 */
public final class JwtTokenVerifier {

    private static final Base64.Decoder DECODER = Base64.getUrlDecoder();
    private static final Base64.Encoder ENCODER = Base64.getUrlEncoder().withoutPadding();

    private final ObjectMapper objectMapper;
    private final byte[] secret;

    public JwtTokenVerifier(ObjectMapper objectMapper, byte[] secret) {
        if (secret == null || secret.length < 32) {
            throw new IllegalArgumentException("JWT verification secret must contain at least 32 bytes");
        }
        this.objectMapper = objectMapper;
        this.secret = secret.clone();
    }

    public AccessTokenClaims verify(String token) {
        try {
            if (token == null || token.length() > 8192) throw invalid();
            String[] parts = token.split("\\.");
            if (parts.length != 3) throw invalid();
            JsonNode header = objectMapper.readTree(DECODER.decode(parts[0]));
            if (!"HS256".equals(header.path("alg").asText()) || !"JWT".equals(header.path("typ").asText())) {
                throw invalid();
            }
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

    private String sign(String value) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret, "HmacSHA256"));
        return ENCODER.encodeToString(mac.doFinal(value.getBytes(StandardCharsets.US_ASCII)));
    }

    private static ApiException invalid() {
        return new ApiException(ErrorCodes.AUTH_UNAUTHENTICATED, HttpStatus.UNAUTHORIZED, "访问令牌无效");
    }

    public record AccessTokenClaims(String userId, String tenantId, String sessionId, String jti,
                                    long authVersion, OffsetDateTime expiresAt) {
    }
}
