"""文本归一化、标准编号/条款号识别与乱码检测。

这些规则是跨格式共享的领域知识，避免 PDF/Word/Markdown 各自实现一套。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 金融/国家标准编号前缀；顺序影响匹配优先度（GB/T 必须先于 GB）。
_STANDARD_PATTERN = (
    r"(?:GB/T|GBZ|GB|JR/T|JR|ISO/IEC|ISO|IEC|ITU-T|ITU|ANSI|SJ/T|SJ|YD/T|YD|"
    r"DB\s?\d{2}/T|DB\s?\d{2}|JJF|JJG|TB/T|TB|CJ/T|CJ|HG/T|HG|SB/T|SB|QX/T|QX|TSG)"
    r"\s*\d{2,5}(?:\.\d{1,3})*(?:\s*[—\-–]\s*\d{2,4})?"
)
STANDARD_NO_RE = re.compile(rf"(?<![A-Za-z0-9])({_STANDARD_PATTERN})", re.IGNORECASE)

_CLAUSE_RE = re.compile(
    r"^[ \t\u3000]*(\d{1,2}(?:\.\d{1,3}){0,5}|[A-Z](?:\.\d{1,3}){1,5})[ \t\u3000]+(\S.{0,199})$"
)
_CHAPTER_RE = re.compile(
    r"^[ \t\u3000]*第[ \t\u3000]*([一二三四五六七八九十百零〇两]{1,4})[ \t\u3000]*章[ \t\u3000]*(\S.{0,199})$"
)
_ANNEX_RE = re.compile(r"^[ \t\u3000]*(附录[ \t\u3000]*[A-Za-z])[ \t\u3000]*[：:]?[ \t\u3000]*(\S.{0,199})$")
_TABLE_CAPTION_RE = re.compile(r"^[ \t\u3000]*(表[ \t\u3000]*[A-Za-z0-9一二三四五六七八九十.\-]*)[ \t\u3000]*(\S.*)?$")

_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_SPACE_RE = re.compile(r"[ \t\u3000]+")

# 乱码判据：控制/私用/未分配字符、替换符以及常见 UTF-8→Latin-1 误解码片段。
_MOJIBAKE_TOKENS = ("Ã", "Â", "â€", "ï¿½", "ã€", "ã‚")


@dataclass(slots=True)
class HeadingMatch:
    """标题匹配结果。"""

    clause_no: str
    title: str
    level: int
    kind: str  # clause | chapter | annex


def normalize_text(text: str) -> str:
    """统一换行与空白，去除零宽字符；保留段落结构。"""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    lines = [_SPACE_RE.sub(" ", line).strip() for line in normalized.split("\n")]
    return "\n".join(line for line in lines)


def normalize_inline(text: str) -> str:
    """单行归一化（用于表格单元格与标题）。"""
    return _SPACE_RE.sub(" ", _ZERO_WIDTH_RE.sub("", text or "")).strip()


def garbled_ratio(text: str) -> tuple[int, float]:
    """返回（异常字符数，异常占比）。

    异常字符：替换符、控制/格式/私用/未分配字符（换行制表除外）、常见误解码片段。
    """
    if not text:
        return 0, 0.0
    bad = 0
    for char in text:
        if char in "\n\r\t":
            continue
        if char == "\ufffd" or unicodedata.category(char) in {"Cc", "Cf", "Co", "Cs", "Cn"}:
            bad += 1
    lowered = text
    for token in _MOJIBAKE_TOKENS:
        bad += lowered.count(token)
    return bad, bad / max(1, len(text))


def extract_standard_no(text: str) -> str | None:
    """从正文前部提取标准编号并规范化，例如 ``GB/T 35273-2020``。"""
    if not text:
        return None
    match = STANDARD_NO_RE.search(text)
    if not match:
        return None
    raw = match.group(1).replace("—", "-").replace("–", "-")
    raw = re.sub(r"\s*-\s*", "-", raw)
    pieces = re.match(r"([A-Za-z/\s]+?)\s*(\d.*)$", raw)
    if not pieces:
        return re.sub(r"\s+", " ", raw).strip()
    prefix = re.sub(r"\s+", "", pieces.group(1)).upper()
    suffix = re.sub(r"\s+", "", pieces.group(2))
    return f"{prefix} {suffix}"


def infer_title(lines: list[str], standard_no: str | None, *, scan_lines: int = 25) -> str:
    """从封面/首段推断文档标题：跳过标准编号、发布机构与日期行。"""
    candidates: list[str] = []
    for line in lines[:scan_lines]:
        value = normalize_inline(line)
        if len(value) < 4 or len(value) > 120:
            continue
        if standard_no and value.replace(" ", "") == standard_no.replace(" ", ""):
            continue
        if re.fullmatch(r"[\d\s./年月日\-—]+", value):
            continue
        if any(token in value for token in ("ICS", "CCS", "中华人民共和国行业标准", "中华人民共和国国家标准",
                                            "发布", "实施", "代替")):
            continue
        if standard_no and value.upper().startswith(standard_no.upper()):
            value = value[len(standard_no):].lstrip(" ：:—-")
            if len(value) < 4:
                continue
        if match_heading(value) is not None:
            continue
        candidates.append(value)
    if not candidates:
        return "未命名标准文档"
    cjk = [value for value in candidates if _cjk_count(value) >= 2]
    pool = cjk or candidates
    # 标准封面标题通常出现在前几行且不含句号；优先取首个候选，避免误选正文长句。
    for value in pool:
        if not value.endswith("。") and len(value) <= 80:
            return value
    return max(pool, key=len)


def match_heading(line: str, *, max_level: int = 6) -> HeadingMatch | None:
    """识别标题行：条款号、章标题或附录。"""
    value = normalize_inline(line)
    if not value or len(value) > 200:
        return None
    chapter = _CHAPTER_RE.match(value)
    if chapter:
        return HeadingMatch(chapter.group(1), chapter.group(2).strip(), 1, "chapter")
    annex = _ANNEX_RE.match(value)
    if annex:
        return HeadingMatch(annex.group(1).replace(" ", ""), annex.group(2).strip(), 1, "annex")
    clause = _CLAUSE_RE.match(value)
    if not clause:
        return None
    number = clause.group(1)
    title = clause.group(2).strip()
    if not _plausible_clause_number(number, title):
        return None
    level = min(max_level, number.count(".") + 1)
    return HeadingMatch(number, title, level, "clause")


def _plausible_clause_number(number: str, title: str) -> bool:
    if not title or title[0] in "、%‰°":
        return False
    if title.endswith("。") and len(title) > 30:
        return False
    if "." not in number:
        try:
            value = int(number)
        except ValueError:
            return False
        if 1900 <= value <= 2100 or value > 30:
            return False
    if len(number.split(".")) > 6:
        return False
    # 纯数字 + 单位（如 "3.5 万元"）更可能是正文金额，不作为条款标题。
    if re.fullmatch(r"[\d.%]+", title):
        return False
    return True


def is_table_caption(line: str) -> bool:
    return bool(_TABLE_CAPTION_RE.match(normalize_inline(line)))


def table_caption_text(line: str) -> str:
    match = _TABLE_CAPTION_RE.match(normalize_inline(line))
    if not match:
        return ""
    return normalize_inline(line)


def chapter_path(stack: list[tuple[str, str]]) -> str:
    """把（编号，标题）栈拼为契约中的 ``chapterPath``。"""
    return " / ".join(f"{number} {title}".strip() for number, title in stack)


def make_section_id(index: int) -> str:
    return f"sec_{index:04d}"


def _cjk_count(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
