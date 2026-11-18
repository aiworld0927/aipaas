package com.aipaas.platform.crypto;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.GeneralSecurityException;
import java.util.Arrays;
import java.util.Base64;

import static org.junit.jupiter.api.Assertions.*;

class EncryptionServiceTest {

    @TempDir
    Path temp;

    private KeyringService keyring;
    private EncryptionService encryption;

    @BeforeEach
    void setUp() throws Exception {
        keyring = new KeyringService(temp.resolve("security/keyring.json").toString(), new ObjectMapper());
        keyring.initialize();
        encryption = new EncryptionService(keyring);
    }

    @Test
    void fieldRoundTripSupportsRotationAndRejectsWrongAad() throws Exception {
        String plaintext = "JR/T 0088 内部绝密文件";
        String oldKid = keyring.activeKeyId();
        String oldCiphertext = encryption.encrypt(plaintext, "doc-1:filename");

        assertFalse(oldCiphertext.contains(plaintext));
        assertEquals(plaintext, encryption.decrypt(oldCiphertext, "doc-1:filename"));
        assertThrows(IllegalStateException.class, () -> encryption.decrypt(oldCiphertext, "doc-2:filename"));

        String newKid = keyring.rotate();
        String newCiphertext = encryption.encrypt(plaintext, "doc-2:filename");
        assertNotEquals(oldKid, newKid);
        assertTrue(oldCiphertext.startsWith("A1." + oldKid + "."));
        assertTrue(newCiphertext.startsWith("A1." + newKid + "."));
        assertEquals(plaintext, encryption.decrypt(oldCiphertext, "doc-1:filename"));
        assertEquals(plaintext, encryption.decrypt(newCiphertext, "doc-2:filename"));
    }

    @Test
    void objectFileIsCiphertextAndTamperingFailsAuthentication() throws Exception {
        byte[] plaintext = "%PDF-1.4\n敏感明文内容不得落盘\n".getBytes(StandardCharsets.UTF_8);
        Path destination = temp.resolve("object.a1");
        encryption.encryptToFile(new ByteArrayInputStream(plaintext), destination, "doc-1:object");

        byte[] stored = Files.readAllBytes(destination);
        assertTrue(new String(stored, 0, 2, StandardCharsets.US_ASCII).equals("A1"));
        assertFalse(contains(stored, plaintext), "最终对象不得包含完整明文");
        assertArrayEquals(plaintext, decryptObject(stored, "doc-1:object"));
        assertThrows(GeneralSecurityException.class,
                () -> decryptObject(stored, "doc-2:object"), "AAD 不匹配必须失败");

        stored[stored.length - 1] ^= 1;
        assertThrows(GeneralSecurityException.class,
                () -> decryptObject(stored, "doc-1:object"), "密文篡改必须失败");
    }

    @Test
    void keyringIsOwnerReadableOnlyOnPosix() throws Exception {
        Path path = temp.resolve("security/keyring.json");
        assertEquals(PosixFilePermissions.fromString("rw-------"), Files.getPosixFilePermissions(path));
        assertEquals(32, keyring.encryptionKey(keyring.activeKeyId()).getEncoded().length);
    }

    private byte[] decryptObject(byte[] stored, String aad) throws GeneralSecurityException {
        int first = indexOf(stored, (byte) '\n', 0);
        int second = indexOf(stored, (byte) '\n', first + 1);
        int third = indexOf(stored, (byte) '\n', second + 1);
        String keyId = new String(stored, first + 1, second - first - 1, StandardCharsets.US_ASCII);
        byte[] nonce = Base64.getDecoder().decode(
                new String(stored, second + 1, third - second - 1, StandardCharsets.US_ASCII));
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, keyring.encryptionKey(keyId), new GCMParameterSpec(128, nonce));
        cipher.updateAAD(aad.getBytes(StandardCharsets.UTF_8));
        return cipher.doFinal(Arrays.copyOfRange(stored, third + 1, stored.length));
    }

    private static int indexOf(byte[] value, byte target, int from) {
        for (int i = from; i < value.length; i++) {
            if (value[i] == target) return i;
        }
        throw new IllegalArgumentException("Invalid encrypted object header");
    }

    private static boolean contains(byte[] haystack, byte[] needle) {
        outer: for (int i = 0; i <= haystack.length - needle.length; i++) {
            for (int j = 0; j < needle.length; j++) {
                if (haystack[i + j] != needle[j]) continue outer;
            }
            return true;
        }
        return false;
    }
}
