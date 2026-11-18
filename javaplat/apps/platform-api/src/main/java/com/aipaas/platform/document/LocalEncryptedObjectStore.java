package com.aipaas.platform.document;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.PosixFilePermissions;

/** 本地密文存储仅用于单元测试和离线验收。 */
@Service
@ConditionalOnProperty(name = "aipaas.storage.type", havingValue = "local")
public class LocalEncryptedObjectStore implements EncryptedObjectStore {

    private final Path root;

    public LocalEncryptedObjectStore(@Value("${aipaas.storage.local-path:/tmp/aipaas-p0/objects}") String root) {
        this.root = Path.of(root).toAbsolutePath().normalize();
    }

    @Override
    public void put(String objectKey, Path ciphertext) {
        try {
            Path destination = resolve(objectKey);
            Files.createDirectories(destination.getParent());
            Files.copy(ciphertext, destination, StandardCopyOption.REPLACE_EXISTING);
            try {
                Files.setPosixFilePermissions(destination, PosixFilePermissions.fromString("rw-------"));
            } catch (UnsupportedOperationException ignored) {
                // 非 POSIX 文件系统由部署侧 ACL 保护。
            }
        } catch (IOException ex) {
            throw new UncheckedIOException(ex);
        }
    }

    @Override
    public InputStream open(String objectKey) {
        try {
            return Files.newInputStream(resolve(objectKey));
        } catch (IOException ex) {
            throw new UncheckedIOException(ex);
        }
    }

    @Override
    public void delete(String objectKey) {
        try {
            Files.deleteIfExists(resolve(objectKey));
        } catch (IOException ex) {
            throw new UncheckedIOException(ex);
        }
    }

    private Path resolve(String key) {
        Path path = root.resolve(key).normalize();
        if (!path.startsWith(root)) throw new IllegalArgumentException("Invalid object key");
        return path;
    }
}
