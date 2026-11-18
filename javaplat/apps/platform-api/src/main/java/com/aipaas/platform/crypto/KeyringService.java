package com.aipaas.platform.crypto;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.SecureRandom;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import javax.crypto.SecretKey;
import javax.crypto.spec.SecretKeySpec;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;

/** 集中文件密钥环（P0 单实例）。所有使用者只拿 kid，不知道其它密钥用途。 */
@Service
public class KeyringService {

    private static final SecureRandom RANDOM = new SecureRandom();
    private final Path path;
    private final ObjectMapper objectMapper;
    private Keyring keyring;

    public KeyringService(@Value("${aipaas.security.keyring-path:/tmp/aipaas-p0/keyring.json}") String path,
                          ObjectMapper objectMapper) {
        this.path = Path.of(path).toAbsolutePath().normalize();
        this.objectMapper = objectMapper;
    }

    @jakarta.annotation.PostConstruct
    synchronized void initialize() throws IOException {
        if (Files.exists(path)) {
            keyring = objectMapper.readValue(Files.readString(path), Keyring.class);
            validate();
        } else {
            keyring = new Keyring();
            keyring.activeKeyId = newKeyId();
            keyring.keys.put(keyring.activeKeyId, randomBase64(32));
            keyring.jwtSecret = randomBase64(48);
            keyring.auditSecret = randomBase64(48);
            keyring.createdAt = now();
            keyring.lastRotatedAt = keyring.createdAt;
            persist();
        }
        restrictPermissions();
    }

    private void validate() {
        if (keyring == null || keyring.activeKeyId == null || keyring.keys == null
                || !keyring.keys.containsKey(keyring.activeKeyId) || keyring.jwtSecret == null
                || keyring.auditSecret == null) {
            throw new IllegalStateException("Invalid security keyring");
        }
        for (String encoded : keyring.keys.values()) {
            if (Base64.getDecoder().decode(encoded).length != 32) {
                throw new IllegalStateException("AES keys must be 256 bits");
            }
        }
    }

    public synchronized String activeKeyId() {
        return keyring.activeKeyId;
    }

    public synchronized SecretKey activeEncryptionKey() {
        return encryptionKey(keyring.activeKeyId);
    }

    public synchronized SecretKey encryptionKey(String keyId) {
        String encoded = keyring.keys.get(keyId);
        if (encoded == null) {
            throw new IllegalArgumentException("Unknown encryption key id");
        }
        return new SecretKeySpec(Base64.getDecoder().decode(encoded), "AES");
    }

    public synchronized byte[] jwtSecret() {
        return Base64.getDecoder().decode(keyring.jwtSecret);
    }

    public synchronized byte[] auditSecret() {
        return Base64.getDecoder().decode(keyring.auditSecret);
    }

    public synchronized String lastRotatedAt() {
        return keyring.lastRotatedAt;
    }

    /** 新增密钥并切换活动 kid；旧密钥保留，历史密文仍可解密。 */
    public synchronized String rotate() throws IOException {
        String kid = newKeyId();
        keyring.keys.put(kid, randomBase64(32));
        keyring.activeKeyId = kid;
        keyring.lastRotatedAt = now();
        persist();
        restrictPermissions();
        return kid;
    }

    private void persist() throws IOException {
        Files.createDirectories(path.getParent());
        Path temp = path.resolveSibling(path.getFileName() + ".tmp");
        objectMapper.writeValue(temp.toFile(), keyring);
        try {
            Files.setPosixFilePermissions(temp, PosixFilePermissions.fromString("rw-------"));
        } catch (UnsupportedOperationException ignored) {
            // 非 POSIX 文件系统由部署侧 ACL 保护。
        }
        Files.move(temp, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }

    private void restrictPermissions() throws IOException {
        try {
            Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-------"));
        } catch (UnsupportedOperationException ignored) {
            // 非 POSIX 文件系统由部署侧 ACL 保护。
        }
    }

    private static String newKeyId() {
        return "k" + UUID.randomUUID().toString().replace("-", "");
    }

    private static String randomBase64(int bytes) {
        byte[] value = new byte[bytes];
        RANDOM.nextBytes(value);
        return Base64.getEncoder().encodeToString(value);
    }

    private static String now() {
        return OffsetDateTime.now(ZoneOffset.UTC).toString();
    }

    static class Keyring {
        public String activeKeyId;
        public Map<String, String> keys = new LinkedHashMap<>();
        public String jwtSecret;
        public String auditSecret;
        public String createdAt;
        public String lastRotatedAt;
    }
}
