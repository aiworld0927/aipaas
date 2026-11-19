package com.aipaas.platform.auth;

import org.springframework.stereotype.Component;

import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;

@Component
public class PasswordPolicy {

    private static final int MIN_LENGTH = 12;
    private static final int MAX_LENGTH = 128;
    private static final Set<String> COMMON = Set.of("password", "qwerty", "admin123", "letmein", "12345678");
    private static final Pattern UPPER = Pattern.compile("[A-Z]");
    private static final Pattern LOWER = Pattern.compile("[a-z]");
    private static final Pattern DIGIT = Pattern.compile("[0-9]");
    private static final Pattern SPECIAL = Pattern.compile("[^A-Za-z0-9]");

    public void validate(String password, String username) {
        if (password == null || password.length() < MIN_LENGTH || password.length() > MAX_LENGTH) {
            throw new IllegalArgumentException("密码长度须为 12–128 位");
        }
        if (!UPPER.matcher(password).find() || !LOWER.matcher(password).find()
                || !DIGIT.matcher(password).find() || !SPECIAL.matcher(password).find()) {
            throw new IllegalArgumentException("密码须同时包含大小写字母、数字和特殊字符");
        }
        String normalized = password.toLowerCase(Locale.ROOT);
        if (COMMON.stream().anyMatch(normalized::contains)
                || (username != null && normalized.contains(username.toLowerCase(Locale.ROOT)))) {
            throw new IllegalArgumentException("密码不得包含常见弱口令或用户名");
        }
        if (hasRepeatedSequence(password)) {
            throw new IllegalArgumentException("密码不得包含 4 位及以上连续或重复字符");
        }
    }

    private boolean hasRepeatedSequence(String value) {
        for (int i = 0; i + 3 < value.length(); i++) {
            char a = value.charAt(i);
            char b = value.charAt(i + 1);
            char c = value.charAt(i + 2);
            char d = value.charAt(i + 3);
            if (a == b && b == c && c == d) {
                return true;
            }
            if (b == a + 1 && c == b + 1 && d == c + 1) {
                return true;
            }
        }
        return false;
    }
}
