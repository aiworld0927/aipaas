package com.aipaas.platform.crypto;

import org.springframework.stereotype.Service;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;

/** AES-256-GCM；kid 与 nonce 随密文保存，AAD 防止跨字段/对象搬移密文。 */
@Service
public class EncryptionService {

    private static final String VERSION = "A1";
    private static final int NONCE_BYTES = 12;
    private static final int TAG_BITS = 128;
    private static final SecureRandom RANDOM = new SecureRandom();
    private final KeyringService keyring;

    public EncryptionService(KeyringService keyring) {
        this.keyring = keyring;
    }

    public String encrypt(String plaintext, String aad) {
        if (plaintext == null) {
            return null;
        }
        try {
            byte[] nonce = nonce();
            String keyId = keyring.activeKeyId();
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, keyring.encryptionKey(keyId), new GCMParameterSpec(TAG_BITS, nonce));
            cipher.updateAAD(aad.getBytes(StandardCharsets.UTF_8));
            byte[] ciphertext = cipher.doFinal(plaintext.getBytes(StandardCharsets.UTF_8));
            return String.join(".", VERSION, keyId, Base64.getEncoder().encodeToString(nonce),
                    Base64.getEncoder().encodeToString(ciphertext));
        } catch (GeneralSecurityException ex) {
            throw new IllegalStateException("Encryption failed", ex);
        }
    }

    public String decrypt(String encoded, String aad) {
        if (encoded == null) {
            return null;
        }
        try {
            String[] parts = encoded.split("\\.");
            if (parts.length != 4 || !VERSION.equals(parts[0])) {
                throw new IllegalArgumentException("Unsupported ciphertext format");
            }
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, keyring.encryptionKey(parts[1]),
                    new GCMParameterSpec(TAG_BITS, Base64.getDecoder().decode(parts[2])));
            cipher.updateAAD(aad.getBytes(StandardCharsets.UTF_8));
            return new String(cipher.doFinal(Base64.getDecoder().decode(parts[3])), StandardCharsets.UTF_8);
        } catch (GeneralSecurityException | IllegalArgumentException ex) {
            throw new IllegalStateException("Decryption failed", ex);
        }
    }

    /** 明文只从输入流读取，落盘文件始终为 AES-GCM 密文。返回写入的明文字节数。 */
    public long encryptToFile(InputStream input, Path destination, String aad) throws IOException {
        try {
            byte[] nonce = nonce();
            String keyId = keyring.activeKeyId();
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, keyring.encryptionKey(keyId), new GCMParameterSpec(TAG_BITS, nonce));
            cipher.updateAAD(aad.getBytes(StandardCharsets.UTF_8));
            try (OutputStream output = Files.newOutputStream(destination)) {
                output.write(VERSION.getBytes(StandardCharsets.US_ASCII));
                output.write('\n');
                output.write(keyId.getBytes(StandardCharsets.US_ASCII));
                output.write('\n');
                output.write(Base64.getEncoder().encode(nonce));
                output.write('\n');
                byte[] buffer = new byte[8192];
                int read;
                long plainBytes = 0;
                while ((read = input.read(buffer)) >= 0) {
                    if (read > 0) {
                        plainBytes += read;
                        byte[] encrypted = cipher.update(buffer, 0, read);
                        if (encrypted != null) {
                            output.write(encrypted);
                        }
                    }
                }
                output.write(cipher.doFinal());
                return plainBytes;
            }
        } catch (GeneralSecurityException ex) {
            throw new IOException("Object encryption failed", ex);
        }
    }

    /** 流式解密已落盘密文；明文只写入调用方指定的临时文件。返回明文字节数。 */
    public long decryptToFile(InputStream sourceCiphertext, Path destination, String aad) throws IOException {
        try (BufferedInputStream input = new BufferedInputStream(sourceCiphertext)) {
            String version = readAsciiLine(input);
            String keyId = readAsciiLine(input);
            String nonce = readAsciiLine(input);
            if (!VERSION.equals(version)) {
                throw new IOException("Unsupported ciphertext version");
            }
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, keyring.encryptionKey(keyId),
                    new GCMParameterSpec(TAG_BITS, Base64.getDecoder().decode(nonce)));
            cipher.updateAAD(aad.getBytes(StandardCharsets.UTF_8));
            byte[] ciphertext = input.readAllBytes();
            byte[] plaintext = cipher.doFinal(ciphertext);
            Files.write(destination, plaintext);
            return plaintext.length;
        } catch (GeneralSecurityException | IllegalArgumentException ex) {
            throw new IOException("Object decryption failed", ex);
        }
    }

    private static String readAsciiLine(InputStream input) throws IOException {
        ByteArrayOutputStream line = new ByteArrayOutputStream();
        int value;
        while ((value = input.read()) >= 0 && value != '\n') {
            line.write(value);
        }
        if (value < 0 && line.size() == 0) {
            throw new IOException("Truncated ciphertext header");
        }
        return line.toString(StandardCharsets.US_ASCII);
    }

    private static byte[] nonce() {
        byte[] value = new byte[NONCE_BYTES];
        RANDOM.nextBytes(value);
        return value;
    }

    /**
     * 分片合并：流式解密已落盘的密文分片并重新加密为单一对象，明文只存在于内存缓冲区。
     * 返回明文字节数与 SHA-256，用于服务端校验客户端声明的 fileHash。
     */
    public MergedObject reencrypt(InputStream sourceCiphertext, Path destination, String sourceAad, String destAad)
            throws IOException {
        try (ObjectMerger merger = newMerger(destination, destAad)) {
            merger.append(sourceCiphertext, sourceAad);
            return merger.finish();
        }
    }

    /** 多分片流式合并写入器；调用方逐片 append，最后 finish。 */
    public ObjectMerger newMerger(Path destination, String destAad) throws IOException {
        return new ObjectMerger(destination, destAad);
    }

    public final class ObjectMerger implements AutoCloseable {
        private final OutputStream output;
        private final Cipher encrypt;
        private final MessageDigest digest;
        private long plainBytes;
        private boolean finished;

        private ObjectMerger(Path destination, String destAad) throws IOException {
            try {
                byte[] destNonce = nonce();
                String destKeyId = keyring.activeKeyId();
                this.encrypt = Cipher.getInstance("AES/GCM/NoPadding");
                this.encrypt.init(Cipher.ENCRYPT_MODE, keyring.encryptionKey(destKeyId),
                        new GCMParameterSpec(TAG_BITS, destNonce));
                this.encrypt.updateAAD(destAad.getBytes(StandardCharsets.UTF_8));
                this.digest = MessageDigest.getInstance("SHA-256");
                this.output = Files.newOutputStream(destination);
                output.write(VERSION.getBytes(StandardCharsets.US_ASCII));
                output.write('\n');
                output.write(destKeyId.getBytes(StandardCharsets.US_ASCII));
                output.write('\n');
                output.write(Base64.getEncoder().encode(destNonce));
                output.write('\n');
            } catch (GeneralSecurityException ex) {
                throw new IOException("Object merge initialization failed", ex);
            }
        }

        public void append(InputStream sourceCiphertext, String sourceAad) throws IOException {
            if (finished) {
                throw new IllegalStateException("Merger already finished");
            }
            try (BufferedInputStream input = new BufferedInputStream(sourceCiphertext)) {
                String version = readLine(input);
                String sourceKeyId = readLine(input);
                String nonceValue = readLine(input);
                if (!VERSION.equals(version)) {
                    throw new IllegalArgumentException("Unsupported ciphertext format");
                }
                Cipher decrypt = Cipher.getInstance("AES/GCM/NoPadding");
                decrypt.init(Cipher.DECRYPT_MODE, keyring.encryptionKey(sourceKeyId),
                        new GCMParameterSpec(TAG_BITS, Base64.getDecoder().decode(nonceValue)));
                decrypt.updateAAD(sourceAad.getBytes(StandardCharsets.UTF_8));
                byte[] buffer = new byte[8192];
                int read;
                while ((read = input.read(buffer)) >= 0) {
                    if (read == 0) {
                        continue;
                    }
                    byte[] plain = decrypt.update(buffer, 0, read);
                    consume(plain);
                }
                consume(decrypt.doFinal());
            } catch (GeneralSecurityException ex) {
                throw new IOException("Object re-encryption failed", ex);
            }
        }

        private void consume(byte[] plain) throws IOException {
            if (plain == null || plain.length == 0) {
                return;
            }
            digest.update(plain);
            plainBytes += plain.length;
            byte[] encrypted = encrypt.update(plain);
            if (encrypted != null) {
                output.write(encrypted);
            }
        }

        public MergedObject finish() throws IOException {
            if (finished) {
                throw new IllegalStateException("Merger already finished");
            }
            try {
                output.write(encrypt.doFinal());
                output.close();
                finished = true;
                return new MergedObject(plainBytes, java.util.HexFormat.of().formatHex(digest.digest()));
            } catch (GeneralSecurityException ex) {
                throw new IOException("Object re-encryption finalization failed", ex);
            }
        }

        @Override
        public void close() {
            if (!finished) {
                try {
                    output.close();
                } catch (IOException ignored) {
                    // 失败路径由调用方补偿删除目标文件。
                }
            }
        }
    }

    private static String readLine(InputStream input) throws IOException {
        ByteArrayOutputStream line = new ByteArrayOutputStream();
        int value;
        while ((value = input.read()) >= 0) {
            if (value == '\n') {
                return line.toString(StandardCharsets.US_ASCII);
            }
            line.write(value);
            if (line.size() > 1024) {
                throw new IOException("Ciphertext header too large");
            }
        }
        throw new IOException("Truncated ciphertext header");
    }

    public record MergedObject(long plainBytes, String sha256) {
    }
}
