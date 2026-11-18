"""#114 输入校验器：空查询、超长、恶意注入在入口拦截。

规则全部由 ``ValidationPolicy`` 配置（可增删/调整阈值），命中即阻断并返回
可理解的原因；审计由生成服务写入 ``security.input.blocked`` 或
``security.prompt_injection.blocked``。校验只做归一与拦截，不改变用户语义。
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Pattern

CATEGORY_INPUT = "input"
CATEGORY_PROMPT_INJECTION = "prompt_injection"

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
_EFFECTIVE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")


@dataclass(frozen=True, slots=True)
class ValidationRule:
    """单条校验规则；``kind`` 决定使用 ``pattern`` 还是长度阈值。"""

    rule_id: str
    kind: str  # empty | length | effective | pattern
    category: str = CATEGORY_INPUT
    reason: str = ""
    pattern: str | None = None
    max_chars: int | None = None
    block: bool = True
    flags: int = re.IGNORECASE

    def __post_init__(self) -> None:
        if self.kind not in {"empty", "length", "effective", "pattern"}:
            raise ValueError(f"不支持的校验规则类型：{self.kind}")
        if self.kind == "pattern" and not self.pattern:
            raise ValueError("pattern 类型规则必须提供正则表达式")
        if self.kind == "length" and (self.max_chars is None or self.max_chars < 1):
            raise ValueError("length 类型规则必须提供正整数 max_chars")

    def compile(self) -> Pattern[str] | None:
        return re.compile(self.pattern, self.flags) if self.pattern else None


@dataclass(slots=True)
class ValidationPolicy:
    """校验策略；默认规则覆盖空查询、超长、无有效字符与常见注入话术。"""

    rules: list[ValidationRule] = field(default_factory=list)
    normalize: bool = True
    max_chars: int = 2000

    def __post_init__(self) -> None:
        if not self.rules:
            self.rules = list(default_rules(self.max_chars))

    @classmethod
    def default(cls, *, max_chars: int = 2000) -> "ValidationPolicy":
        return cls(rules=list(default_rules(max_chars)), max_chars=max_chars)

    def with_extra_rules(self, rules: list[ValidationRule]) -> "ValidationPolicy":
        return ValidationPolicy(
            rules=[*self.rules, *rules], normalize=self.normalize, max_chars=self.max_chars
        )


def default_rules(max_chars: int = 2000) -> tuple[ValidationRule, ...]:
    """默认规则：先拦截空/超长，再拦截注入话术（顺序即优先级）。"""
    return (
        ValidationRule("EMPTY_QUERY", "empty", reason="查询内容不能为空"),
        ValidationRule(
            "QUERY_TOO_LONG",
            "length",
            max_chars=max_chars,
            reason=f"查询长度不能超过 {max_chars} 字符",
        ),
        ValidationRule(
            "NO_EFFECTIVE_CHAR",
            "effective",
            reason="查询缺少有效文字或数字",
        ),
        ValidationRule(
            "INJ_IGNORE_PREVIOUS",
            "pattern",
            category=CATEGORY_PROMPT_INJECTION,
            reason="检测到“忽略既有指令”类 Prompt 注入",
            pattern=r"(忽略|无视|忘掉|跳过).{0,10}(指令|提示|规则|要求)"
            r"|ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?)",
        ),
        ValidationRule(
            "INJ_SYSTEM_PROMPT",
            "pattern",
            category=CATEGORY_PROMPT_INJECTION,
            reason="检测到探测/泄露系统提示词的注入",
            pattern=r"(系统提示词|系统指令|system\s*prompt|developer\s*message)"
            r"|(reveal|show|print|repeat|dump).{0,20}(system\s*prompt|instructions?|prompt)",
        ),
        ValidationRule(
            "INJ_ROLE_HIJACK",
            "pattern",
            category=CATEGORY_PROMPT_INJECTION,
            reason="检测到角色劫持类 Prompt 注入",
            pattern=r"(你现在是|从现在开始你|假装你是|扮演.{0,10}(管理员|开发者|超级用户))"
            r"|\byou\s+are\s+now\b",
        ),
        ValidationRule(
            "INJ_JAILBREAK",
            "pattern",
            category=CATEGORY_PROMPT_INJECTION,
            reason="检测到越狱模式类 Prompt 注入",
            pattern=r"(jailbreak|dan\s*mode|developer\s*mode|越狱模式)",
        ),
        ValidationRule(
            "INJ_CHAT_TEMPLATE",
            "pattern",
            category=CATEGORY_PROMPT_INJECTION,
            reason="检测到对话模板标记注入",
            pattern=r"<\|im_start\|>|<\|system\|>|<\|endoftext\|>",
        ),
        ValidationRule(
            "INJ_SECRET_EXFIL",
            "pattern",
            category=CATEGORY_PROMPT_INJECTION,
            reason="检测到索取密钥/凭据的注入",
            pattern=r"(泄露|输出|告诉我).{0,8}(api[_ -]?key|密钥|密码|口令|token)",
        ),
    )


@dataclass(slots=True)
class ValidationResult:
    """校验结果：``blocked=false`` 时才使用 ``normalized_query`` 进入检索。"""

    ok: bool
    normalized_query: str
    blocked: bool = False
    rule_id: str | None = None
    category: str | None = None
    reason: str | None = None
    matched_text: str | None = None
    original_length: int = 0
    normalized_length: int = 0
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "normalizedQuery": self.normalized_query,
            "blocked": self.blocked,
            "ruleId": self.rule_id,
            "category": self.category,
            "reason": self.reason,
            "matchedText": self.matched_text,
            "originalLength": self.original_length,
            "normalizedLength": self.normalized_length,
            "latencyMs": round(self.latency_ms, 3),
        }


class InputValidator:
    """可配置入口校验器；不抛异常，由调用方决定阻断行为与审计。"""

    def __init__(self, policy: ValidationPolicy | None = None) -> None:
        self.policy = policy or ValidationPolicy.default()
        self._compiled: list[tuple[ValidationRule, Pattern[str] | None]] = [
            (rule, rule.compile()) for rule in self.policy.rules
        ]

    def normalize(self, query: str) -> str:
        text = unicodedata.normalize("NFKC", query or "")
        text = _ZERO_WIDTH.sub("", text)
        text = _CONTROL.sub(" ", text)
        text = text.replace("\u3000", " ")
        return _WHITESPACE.sub(" ", text).strip()

    def validate(self, query: str) -> ValidationResult:
        started = time.perf_counter()
        original = query or ""
        normalized = self.normalize(original) if self.policy.normalize else original.strip()
        for rule, pattern in self._compiled:
            matched = self._match(rule, pattern, normalized)
            if matched is None:
                continue
            blocked = rule.block
            return ValidationResult(
                ok=not blocked,
                normalized_query=normalized,
                blocked=blocked,
                rule_id=rule.rule_id,
                category=rule.category,
                reason=rule.reason or rule.rule_id,
                matched_text=matched[:40] if matched else None,
                original_length=len(original),
                normalized_length=len(normalized),
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        return ValidationResult(
            ok=True,
            normalized_query=normalized,
            original_length=len(original),
            normalized_length=len(normalized),
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    @staticmethod
    def _match(rule: ValidationRule, pattern: Pattern[str] | None, text: str) -> str | None:
        if rule.kind == "empty":
            return "" if not text else None
        if rule.kind == "length":
            return text if len(text) > int(rule.max_chars or 0) else None
        if rule.kind == "effective":
            return None if _EFFECTIVE.search(text) else text
        if pattern is None:
            return None
        found = pattern.search(text)
        return found.group(0) if found else None
