"""#63 基础查询处理：标准编号/条款编号归一、清洗、分词与兜底。

处理顺序（全部为纯函数，便于单测与回放）：

1. 移除控制字符/零宽字符并记录告警；
2. ``NFKC`` 规范化（全角数字/标点 → 半角，``４.１.１`` → ``4.1.1``）；
3. 压缩空白、按上限截断（超长兜底）；
4. 归一标准编号（``jrt 0088`` → ``JR/T 0088``）与条款编号（``第4.1.1条`` → ``4.1.1``）；
5. 轻量分词：ASCII 词/数字 + 中文二元组，去重并限量。

耗时由 ``QueryUnderstanding.latency_ms`` 返回，检索服务会把它计入检索 P95。
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass

MAX_QUERY_CHARS = 2000
MAX_TOKENS = 64

#: 告警码：稳定前缀便于客户端与测试断言，冒号后为人类可读说明。
WARN_EMPTY = "QUERY_EMPTY"
WARN_TRUNCATED = "QUERY_TRUNCATED"
WARN_SPECIAL_CHARS = "QUERY_SPECIAL_CHARS_REMOVED"
WARN_NO_TOKEN = "QUERY_NO_EFFECTIVE_TOKEN"

_CONTROL_RE = re.compile(
    r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f"
    r"\u200b-\u200f\u202a-\u202e\u2060\ufeff]"
)
_WS_RE = re.compile(r"[\s\u3000]+")

# 标准编号：GB/T 35273-2020、JR/T 0088、ISO/IEC 27001:2013、DB11/T 1234-2020 ...
_STANDARD_NO_RE = re.compile(
    r"(?P<prefix>GB\s*/\s*[TZ]|GBT|GBZ|JR\s*/\s*T|JRT|ISO\s*/\s*IEC|ISOIEC|ISO|IEC|"
    r"YD\s*/\s*T|YDT|SJ\s*/\s*T|SJT|DB\d{2}\s*/\s*T|DB\d{2}|JR|GB)"
    r"\s*[-—–]?\s*(?P<code>\d+(?:\.\d+)*)"
    r"(?:\s*[-—–:：]\s*(?P<year>(?:19|20)\d{2}))?",
    re.IGNORECASE,
)
_PREFIX_ALIASES = {
    "GBT": "GB/T",
    "GBZ": "GB/Z",
    "JRT": "JR/T",
    "ISOIEC": "ISO/IEC",
    "YDT": "YD/T",
    "SJT": "SJ/T",
}

# 条款编号：优先匹配「第4.1.1条」，其次裸编号（至少一个点，避免命中年份）
_CLAUSE_CN_RE = re.compile(r"第\s*(?P<clause>\d{1,3}(?:\.\d{1,4}){1,4})\s*[条款节项]")
_CLAUSE_BARE_RE = re.compile(r"(?<![\d.A-Za-z])(?P<clause>\d{1,2}(?:\.\d{1,3}){1,4})(?![\d.])")

_ASCII_TOKEN_RE = re.compile(r"[A-Za-z]+|\d+(?:\.\d+)*")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


@dataclass(slots=True)
class QueryUnderstanding:
    """查询归一结果；``latency_ms`` 为该步骤耗时（计入检索总耗时）。"""

    original_query: str
    normalized_query: str
    standard_no: str | None
    clause_no: str | None
    tokens: tuple[str, ...]
    warnings: tuple[str, ...]
    latency_ms: float
    truncated: bool = False

    @property
    def is_blank(self) -> bool:
        return not self.normalized_query

    @property
    def has_anchor(self) -> bool:
        return bool(self.standard_no or self.clause_no)

    def to_dict(self) -> dict[str, object]:
        return {
            "originalQuery": self.original_query,
            "normalizedQuery": self.normalized_query,
            "standardNo": self.standard_no,
            "clauseNo": self.clause_no,
            "tokens": list(self.tokens),
            "warnings": list(self.warnings),
            "latencyMs": round(self.latency_ms, 3),
            "truncated": self.truncated,
        }


def _canonical_prefix(raw: str) -> str:
    prefix = re.sub(r"\s+", "", raw).upper().replace("／", "/")
    return _PREFIX_ALIASES.get(prefix, prefix)


def normalize_standard_no(value: str | None) -> str | None:
    """抽取并归一标准编号；无法识别时返回 ``None``。

    例：``jrt 0088`` → ``JR/T 0088``；``GB/T35273—2020`` → ``GB/T 35273-2020``。
    """
    if not value:
        return None
    text = unicodedata.normalize("NFKC", str(value))
    match = _STANDARD_NO_RE.search(text)
    if match is None:
        return None
    prefix = _canonical_prefix(match.group("prefix"))
    code = match.group("code")
    year = match.group("year")
    return f"{prefix} {code}" + (f"-{year}" if year else "")


def standard_no_key(value: str | None) -> str | None:
    """标准编号比较键：去掉空白、统一大写，用于精确/前缀匹配。"""
    normalized = normalize_standard_no(value)
    return re.sub(r"\s+", "", normalized).upper() if normalized else None


def standard_no_matches(record_value: str | None, query_value: str | None) -> bool:
    """标准编号匹配：完全一致，或查询省略年份时按前缀匹配（``JR/T 0088`` ⊂ ``JR/T 0088-2016``）。"""
    record_key = standard_no_key(record_value)
    query_key = standard_no_key(query_value)
    if record_key is None or query_key is None:
        return query_key is None
    return record_key == query_key or record_key.startswith(f"{query_key}-")


def normalize_clause_no(value: str | None) -> str | None:
    """抽取并归一单一条款编号；``第4.1.1条``/``４.１.１`` 均归一为 ``4.1.1``。"""
    if not value:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    match = _CLAUSE_CN_RE.search(text) or _CLAUSE_BARE_RE.search(text)
    return match.group("clause") if match else None


def clause_no_matches(record_value: str | None, query_value: str | None) -> bool:
    record_clause = normalize_clause_no(record_value)
    query_clause = normalize_clause_no(query_value)
    if query_clause is None:
        return True
    return record_clause == query_clause


def _rewrite_standard_numbers(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return normalize_standard_no(match.group(0)) or match.group(0)

    return _STANDARD_NO_RE.sub(replace, text)


def _rewrite_clause_numbers(text: str) -> str:
    text = _CLAUSE_CN_RE.sub(lambda match: match.group("clause"), text)
    return _CLAUSE_BARE_RE.sub(lambda match: match.group("clause"), text)


def tokenize(text: str, limit: int = MAX_TOKENS) -> tuple[str, ...]:
    """轻量分词：ASCII 词/数字 + 中文二元组（单字词保留），按出现顺序去重。"""
    tokens: list[str] = []
    seen: set[str] = set()

    def push(token: str) -> None:
        if token and token not in seen and len(tokens) < limit:
            seen.add(token)
            tokens.append(token)

    for match in _ASCII_TOKEN_RE.finditer(text):
        token = match.group(0).lower()
        # 跳过标准编号前缀拆分出的单字母（如 JR/T 的 t）
        if len(token) == 1 and not token.isdigit():
            continue
        push(token)
        if len(tokens) >= limit:
            return tuple(tokens)

    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            push(run)
            continue
        for index in range(len(run) - 1):
            push(run[index : index + 2])
            if len(tokens) >= limit:
                return tuple(tokens)
    return tuple(tokens)


def _anchor_only_text(text: str, standard_no: str | None, clause_no: str | None) -> str:
    """去掉编号锚点后的剩余文本，用于判断查询是否只剩编号。"""
    remainder = _STANDARD_NO_RE.sub(" ", text) if standard_no else text
    if clause_no:
        remainder = _CLAUSE_CN_RE.sub(" ", remainder)
        remainder = _CLAUSE_BARE_RE.sub(" ", remainder)
    return remainder


def understand_query(raw: str | None, *, max_length: int = MAX_QUERY_CHARS) -> QueryUnderstanding:
    """清洗、归一、分词并给出兜底告警；任何输入都不抛异常。"""
    started = time.perf_counter()
    original = "" if raw is None else str(raw)
    warnings: list[str] = []

    cleaned = _CONTROL_RE.sub("", original)
    if cleaned != original:
        warnings.append(f"{WARN_SPECIAL_CHARS}: 已移除控制字符/零宽字符")

    text = unicodedata.normalize("NFKC", cleaned)
    text = _WS_RE.sub(" ", text).strip()

    truncated = len(text) > max_length
    if truncated:
        text = text[:max_length].rstrip()
        warnings.append(f"{WARN_TRUNCATED}: 查询超过 {max_length} 字符，已截断")

    if not text:
        warnings.append(f"{WARN_EMPTY}: 查询为空，将返回空结果")
        return QueryUnderstanding(
            original_query=original,
            normalized_query="",
            standard_no=None,
            clause_no=None,
            tokens=(),
            warnings=tuple(warnings),
            latency_ms=(time.perf_counter() - started) * 1000,
            truncated=truncated,
        )

    normalized = _rewrite_standard_numbers(text)
    normalized = _rewrite_clause_numbers(normalized)
    standard_no = normalize_standard_no(normalized)
    clause_no = normalize_clause_no(normalized)

    remainder = _anchor_only_text(normalized, standard_no, clause_no)
    tokens = tokenize(remainder)
    if not tokens and not (standard_no or clause_no):
        warnings.append(f"{WARN_NO_TOKEN}: 清洗后无可检索内容，将返回空结果")

    return QueryUnderstanding(
        original_query=original,
        normalized_query=normalized,
        standard_no=standard_no,
        clause_no=clause_no,
        tokens=tokens,
        warnings=tuple(warnings),
        latency_ms=(time.perf_counter() - started) * 1000,
        truncated=truncated,
    )
