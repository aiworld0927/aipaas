package com.aipaas.platform.auth;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

class PasswordPolicyTest {

    private final PasswordPolicy policy = new PasswordPolicy();

    @Test
    void acceptsStrongPassword() {
        assertDoesNotThrow(() -> policy.validate("S3cure!Vault#2026", "admin"));
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "Short1!A",
            "alllowercase1!xx",
            "ALLUPPERCASE1!XX",
            "NoDigits!ValidX",
            "NoSpecial123abc",
            "Str0ng!PasswordX",
            "S3cure!xxxx2026",
            "S3cure!2345xyz"
    })
    void rejectsWeakPasswords(String password) {
        assertThrows(IllegalArgumentException.class, () -> policy.validate(password, "admin"));
    }

    @Test
    void rejectsUsernameAndNull() {
        assertThrows(IllegalArgumentException.class, () -> policy.validate("S3cure!Vault#2026", "vault"));
        assertThrows(IllegalArgumentException.class, () -> policy.validate(null, "admin"));
    }
}
