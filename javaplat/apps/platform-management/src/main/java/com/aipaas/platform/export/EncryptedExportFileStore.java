package com.aipaas.platform.export;

import com.aipaas.platform.crypto.EncryptionService;
import com.aipaas.platform.document.EncryptedObjectStore;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;

/** 导出文件以 AES-GCM 密文写入对象存储；明文只存在于受限临时文件与内存。 */
@Service
public class EncryptedExportFileStore implements ExportFileStore {

    private final EncryptedObjectStore storage;
    private final EncryptionService encryption;
    private final Path tempDirectory;

    public EncryptedExportFileStore(EncryptedObjectStore storage, EncryptionService encryption,
                                    @Value("${aipaas.storage.temp-path:/tmp/aipaas-p0/uploads}") String tempDirectory) {
        this.storage = storage;
        this.encryption = encryption;
        this.tempDirectory = Path.of(tempDirectory).toAbsolutePath().normalize();
    }

    @Override
    public void put(String objectKey, Path plaintext) {
        Path ciphertext = null;
        try {
            Files.createDirectories(tempDirectory);
            ciphertext = Files.createTempFile(tempDirectory, "export-", ".a1");
            try (InputStream input = Files.newInputStream(plaintext)) {
                encryption.encryptToFile(input, ciphertext, aad(objectKey));
            }
            storage.put(objectKey, ciphertext);
        } catch (IOException ex) {
            throw new UncheckedIOException("导出文件写入对象存储失败", ex);
        } finally {
            deleteQuietly(ciphertext);
        }
    }

    @Override
    public byte[] read(String objectKey) {
        Path plaintext = null;
        try {
            Files.createDirectories(tempDirectory);
            plaintext = Files.createTempFile(tempDirectory, "export-read-", ".tmp");
            try (InputStream ciphertext = storage.open(objectKey)) {
                encryption.decryptToFile(ciphertext, plaintext, aad(objectKey));
            }
            return Files.readAllBytes(plaintext);
        } catch (IOException ex) {
            throw new UncheckedIOException("导出文件读取失败", ex);
        } finally {
            deleteQuietly(plaintext);
        }
    }

    @Override
    public void delete(String objectKey) {
        storage.delete(objectKey);
    }

    private static String aad(String objectKey) {
        return "export:" + objectKey;
    }

    private static void deleteQuietly(Path path) {
        if (path == null) {
            return;
        }
        try {
            Files.deleteIfExists(path);
        } catch (IOException ignored) {
            // 临时文件清理失败不掩盖主流程异常
        }
    }
}
