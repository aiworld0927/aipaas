"""#57 金融标准元数据标注：六要素抽取、过滤查询与审计。

P0 只处理金融标准六要素：
``standardNo``（标准编号）、``standardName``（标准名称）、``issuer``（发布机构）、
``effectiveAt``（实施日期）、``clauseNo``（条款编号，落在 chunk 上）、``sourceDocument``（来源文档）。

抽取策略：**规则优先，LLM 兜底**。每条字段都带 ``evidence``，记录规则名/定位片段，
LLM 结果额外记录模型与 prompt/response 摘要，可回溯到来源；规则命中的字段不会被 LLM 覆盖。

chunk.meta 同时预留 ``securityLevel/domain/effectiveAt``（P0 为空，不参与过滤，供 P2 使用）。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from aipaas_pipeline.chunking import Chunk
from aipaas_pipeline.models import ParsedDocument

METADATA_RULES_VERSION = "p0-meta-v1"
SOURCE_AUTO = "AUTO_EXTRACTED"
SOURCE_MANUAL = "MANUAL_CORRECTED"
REQUIRED_FIELDS = ("standardNo", "standardName", "issuer", "effectiveAt", "sourceDocument")
EDITABLE_FIELDS = (
    "standardNo",
    "standardName",
    "issuer",
    "issuedAt",
    "effectiveAt",
    "replacedBy",
    "domain",
    "securityLevel",
    "confidence",
)
RESERVED_META_FIELDS = ("securityLevel", "domain", "effectiveAt")

#: StandardMeta 以 JSON 对象形式对外输出（契约字段见 openapi StandardMeta）。
StandardMeta = dict[str, Any]

# 标准号：GB/T 35273-2020、JR/T 0197-2020、ISO/IEC 27001:2013、DB11/T 1234-2020 等
_STANDARD_NO_RE = re.compile(
    r"(?P<no>(?:GB/T|GB/Z|GB|JR/T|JR|YB/T|DB\d*/T|DB\d*|SJ/T|YD/T|ISO/IEC|ISO|IEC)"
    r"\s*\d+(?:\.\d+)*\s*[-—–:：]\s*\d{4})",
    re.IGNORECASE,
)
_DATE_CN_RE = re.compile(
    r"(?P<year>\d{4})\s*[-年/.]\s*(?P<month>\d{1,2})\s*[-月/.]\s*(?P<day>\d{1,2})\s*日?"
)
_EFFECTIVE_KEYS = ("实施日期", "实施时间", "于", "自")
_ISSUED_KEYS = ("发布日期", "发布时间", "发布")

_ISSUER_RULES: tuple[tuple[str, str], ...] = (
    (r"中国人民银行", "中国人民银行"),
    (r"国家标准化管理委员会", "国家标准化管理委员会"),
    (r"全国金融标准化技术委员会", "全国金融标准化技术委员会"),
    (r"中国银行保险监督管理委员会", "中国银行保险监督管理委员会"),
    (r"国家金融监督管理总局", "国家金融监督管理总局"),
    (r"中国证券监督管理委员会", "中国证券监督管理委员会"),
    (r"国家市场监督管理总局", "国家市场监督管理总局"),
    (r"工业和信息化部", "工业和信息化部"),
    (r"公安部", "公安部"),
    (r"国家密码管理局", "国家密码管理局"),
)


class LlmMetadataExtractor(Protocol):
    """LLM 兜底抽取器：仅对缺失字段返回结果，结果需可回溯。"""

    def __call__(self, document: ParsedDocument, missing: list[str]) -> Mapping[str, Any]: ...


@dataclass(slots=True)
class MetaFilter:
    """元数据过滤条件：支持标准号、发布机构、实施时间区间与关键词。"""

    standard_no: str | None = None
    issuer: str | None = None
    effective_from: str | date | datetime | None = None
    effective_to: str | date | datetime | None = None
    keyword: str | None = None
    domain: str | None = None

    def matches(self, meta: Mapping[str, Any]) -> bool:
        if self.standard_no and _norm_standard_no(str(meta.get("standardNo") or "")) != _norm_standard_no(
            self.standard_no
        ):
            return False
        if self.issuer and self.issuer.casefold() not in str(meta.get("issuer") or "").casefold():
            return False
        effective = _as_date(meta.get("effectiveAt"))
        if self.effective_from:
            start = _as_date(self.effective_from)
            if effective is None or start is None or effective < start:
                return False
        if self.effective_to:
            end = _as_date(self.effective_to)
            if effective is None or end is None or effective > end:
                return False
        if self.domain and self.domain.casefold() not in str(meta.get("domain") or "").casefold():
            return False
        if self.keyword:
            haystack = " ".join(
                str(meta.get(key) or "")
                for key in ("standardNo", "standardName", "issuer", "sourceDocument")
            ).casefold()
            if self.keyword.casefold() not in haystack:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "standardNo": self.standard_no,
            "issuer": self.issuer,
            "effectiveFrom": _iso(self.effective_from),
            "effectiveTo": _iso(self.effective_to),
            "keyword": self.keyword,
            "domain": self.domain,
        }


class AnnotationStore(Protocol):
    def save(self, meta: dict[str, Any]) -> None: ...

    def get(self, document_id: str) -> dict[str, Any] | None: ...

    def list(self) -> list[dict[str, Any]]: ...

    def append_audit(self, entry: dict[str, Any]) -> None: ...

    def list_audit(self, document_id: str | None = None) -> list[dict[str, Any]]: ...


class InMemoryAnnotationStore:
    def __init__(self) -> None:
        self._metas: dict[str, dict[str, Any]] = {}
        self._audit: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def save(self, meta: dict[str, Any]) -> None:
        with self._lock:
            self._metas[meta["documentId"]] = json.loads(json.dumps(meta, ensure_ascii=False))

    def get(self, document_id: str) -> dict[str, Any] | None:
        with self._lock:
            meta = self._metas.get(document_id)
            return json.loads(json.dumps(meta, ensure_ascii=False)) if meta else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [json.loads(json.dumps(meta, ensure_ascii=False)) for meta in self._metas.values()]

    def append_audit(self, entry: dict[str, Any]) -> None:
        with self._lock:
            entry = dict(entry)
            entry["seq"] = len(self._audit) + 1
            self._audit.append(entry)

    def list_audit(self, document_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            entries = [
                json.loads(json.dumps(entry, ensure_ascii=False))
                for entry in self._audit
                if document_id is None or entry.get("documentId") == document_id
            ]
            return entries


class FileAnnotationStore:
    """``<root>/<docId>.json`` 存元数据，``<root>/audit.jsonl`` 追加审计（只增不改）。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()

    def save(self, meta: dict[str, Any]) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            target = self.root / f"{_safe(meta['documentId'])}.json"
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(target)

    def get(self, document_id: str) -> dict[str, Any] | None:
        target = self.root / f"{_safe(document_id)}.json"
        if not target.exists():
            return None
        return json.loads(target.read_text(encoding="utf-8"))

    def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.root.glob("*.json"))
            if path.is_file()
        ]

    def append_audit(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / "audit.jsonl"
            seq = sum(1 for _ in path.open("r", encoding="utf-8")) + 1 if path.exists() else 1
            record = {**entry, "seq": seq}
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def list_audit(self, document_id: str | None = None) -> list[dict[str, Any]]:
        path = self.root / "audit.jsonl"
        if not path.exists():
            return []
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if document_id is None:
            return entries
        return [entry for entry in entries if entry.get("documentId") == document_id]


class AnnotationService:
    """元数据标注服务：规则优先 + LLM 兜底，人工修正留痕。"""

    def __init__(
        self,
        store: AnnotationStore | None = None,
        llm: LlmMetadataExtractor | None = None,
    ) -> None:
        self.store = store or InMemoryAnnotationStore()
        self.llm = llm

    # ------------------------------------------------------------------ 标注

    def annotate(self, document: ParsedDocument, llm: LlmMetadataExtractor | None = None) -> dict[str, Any]:
        """抽取六要素并返回 StandardMeta（不落库）；规则命中字段不会被 LLM 覆盖。"""
        evidence: dict[str, dict[str, Any]] = {}
        values: dict[str, Any] = {key: None for key in EDITABLE_FIELDS}
        values["clauseCount"] = len(document.clauses)
        values["sourceDocument"] = None

        self._rule_standard_no(document, values, evidence)
        self._rule_standard_name(document, values, evidence)
        self._rule_issuer(document, values, evidence)
        self._rule_dates(document, values, evidence)
        values["sourceDocument"] = self._source_document(document)
        if values["sourceDocument"]:
            evidence["sourceDocument"] = _evidence(
                values["sourceDocument"], "rule", "source_document", "document", values["sourceDocument"]
            )

        missing = [key for key in REQUIRED_FIELDS if not values.get(key)]
        extractor = llm or self.llm
        if missing and extractor is not None:
            self._apply_llm(document, extractor, missing, values, evidence)

        for key in REQUIRED_FIELDS:
            if key not in evidence:
                evidence[key] = {"value": values.get(key), "extractor": "none", "rule": None, "snippet": ""}

        confidence = _confidence(values, evidence)
        meta = {
            "documentId": document.doc_id,
            "tenantId": document.tenant_id or "default",
            "standardNo": values.get("standardNo") or "",
            "standardName": values.get("standardName") or "",
            "issuer": values.get("issuer") or "",
            "issuedAt": values.get("issuedAt"),
            "effectiveAt": values.get("effectiveAt"),
            "replacedBy": values.get("replacedBy"),
            "domain": values.get("domain"),
            "securityLevel": values.get("securityLevel"),
            "clauseCount": values["clauseCount"],
            "confidence": confidence,
            "source": SOURCE_AUTO,
            "version": None,
            "env": None,
            "rulesVersion": METADATA_RULES_VERSION,
            "documentVersion": document.version,
            "sourceDocument": values.get("sourceDocument"),
            "evidence": evidence,
            "updatedAt": _now(),
        }
        return meta

    def annotate_and_save(
        self,
        document: ParsedDocument,
        llm: LlmMetadataExtractor | None = None,
        *,
        actor: str = "system",
        reason: str = "parse",
    ) -> dict[str, Any]:
        meta = self.annotate(document, llm)
        before = self.store.get(document.doc_id)
        self.store.save(meta)
        self.store.append_audit(
            {
                "at": _now(),
                "documentId": document.doc_id,
                "tenantId": meta["tenantId"],
                "action": "meta.annotate",
                "actor": actor,
                "reason": reason,
                "before": before,
                "after": meta,
            }
        )
        return meta

    def decorate_chunks(self, document: ParsedDocument, chunks: Iterable[Chunk], meta: Mapping[str, Any]) -> None:
        """把文档级元数据快照与 P2 预留字段写入 chunk.meta（条款编号保留在 chunk 上）。"""
        snapshot = {
            "standardNo": meta.get("standardNo"),
            "standardName": meta.get("standardName"),
            "issuer": meta.get("issuer"),
            "effectiveAt": meta.get("effectiveAt"),
            "sourceDocument": meta.get("sourceDocument"),
        }
        for chunk in chunks:
            chunk.meta["documentMetadata"] = dict(snapshot)
            chunk.meta["metadataConfidence"] = meta.get("confidence")
            chunk.meta["clauseNo"] = chunk.clause_no
            for key in RESERVED_META_FIELDS:
                chunk.meta.setdefault(key, None)

    # ------------------------------------------------------------------ 查询 / 修正 / 审计

    def get(self, document_id: str) -> dict[str, Any] | None:
        return self.store.get(document_id)

    def query(self, meta_filter: MetaFilter | None = None) -> list[dict[str, Any]]:
        meta_filter = meta_filter or MetaFilter()
        return [meta for meta in self.store.list() if meta_filter.matches(meta)]

    def audit(self, document_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_audit(document_id)

    def update(
        self,
        document_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str = "system",
        reason: str = "manual_correction",
    ) -> dict[str, Any]:
        before = self.store.get(document_id)
        if before is None:
            raise KeyError(f"元数据不存在：{document_id}")
        unknown = [key for key in patch if key not in EDITABLE_FIELDS]
        if unknown:
            raise ValueError(f"不支持修改的字段：{unknown}")
        if not patch:
            raise ValueError("至少提供一个待修改字段")
        after = dict(before)
        for key, value in patch.items():
            after[key] = _validate_field(key, value)
        after["source"] = SOURCE_MANUAL
        after["updatedAt"] = _now()
        after.setdefault("evidence", {})
        for key in patch:
            after["evidence"][key] = _evidence(
                after.get(key), "manual", "manual_correction", "manual", str(after.get(key) or "")
            )
        self.store.save(after)
        self.store.append_audit(
            {
                "at": _now(),
                "documentId": document_id,
                "tenantId": after.get("tenantId", "default"),
                "action": "meta.update",
                "actor": actor,
                "reason": reason,
                "before": before,
                "after": after,
            }
        )
        return after

    # ------------------------------------------------------------------ 规则实现

    @staticmethod
    def _rule_standard_no(
        document: ParsedDocument, values: dict[str, Any], evidence: dict[str, Any]
    ) -> None:
        if document.standard_no:
            values["standardNo"] = _norm_standard_no(document.standard_no)
            evidence["standardNo"] = _evidence(
                values["standardNo"], "rule", "document.standard_no", "document", document.standard_no
            )
            return
        for location, text in _candidate_texts(document):
            match = _STANDARD_NO_RE.search(text)
            if match:
                values["standardNo"] = _norm_standard_no(match.group("no"))
                evidence["standardNo"] = _evidence(
                    values["standardNo"], "rule", "standard_no_regex", location, match.group(0)
                )
                return

    @staticmethod
    def _rule_standard_name(
        document: ParsedDocument, values: dict[str, Any], evidence: dict[str, Any]
    ) -> None:
        title = (document.title or "").strip()
        if title:
            name = _STANDARD_NO_RE.sub("", title).strip(" -—–:：")
            if name:
                values["standardName"] = name
                evidence["standardName"] = _evidence(name, "rule", "document.title", "title", title)
                return
        for line in (document.raw_text or "").splitlines():
            line = line.strip()
            if not line or _STANDARD_NO_RE.search(line) or len(line) < 4:
                continue
            if any(key in line for key in ("发布", "实施", "前言")):
                continue
            values["standardName"] = line[:128]
            evidence["standardName"] = _evidence(values["standardName"], "rule", "first_heading", "rawText", line)
            return

    @staticmethod
    def _rule_issuer(document: ParsedDocument, values: dict[str, Any], evidence: dict[str, Any]) -> None:
        text = (document.raw_text or "")[:5000]
        best: tuple[int, str, str] | None = None
        for pattern, canonical in _ISSUER_RULES:
            match = re.search(pattern, text)
            if match and (best is None or match.start() < best[0]):
                best = (match.start(), canonical, text[max(0, match.start() - 20): match.end() + 20])
        if best:
            values["issuer"] = best[1]
            evidence["issuer"] = _evidence(best[1], "rule", "issuer_keyword", "rawText", best[2])
            return
        for pattern, canonical in _ISSUER_RULES:
            if re.search(pattern, document.title or ""):
                values["issuer"] = canonical
                evidence["issuer"] = _evidence(canonical, "rule", "issuer_keyword", "title", document.title)
                return

    @staticmethod
    def _rule_dates(document: ParsedDocument, values: dict[str, Any], evidence: dict[str, Any]) -> None:
        text = (document.raw_text or "")[:5000]
        issued = _find_labelled_date(text, _ISSUED_KEYS)
        effective = _find_labelled_date(text, _EFFECTIVE_KEYS)
        if issued:
            values["issuedAt"] = issued[0]
            evidence["issuedAt"] = _evidence(issued[0], "rule", "issued_date", "rawText", issued[1])
        if effective:
            values["effectiveAt"] = effective[0]
            evidence["effectiveAt"] = _evidence(effective[0], "rule", "effective_date", "rawText", effective[1])
        else:
            # 兜底：取文本中最后一个日期作为实施日期（标准封面通常先发布、后实施）
            matches = list(_DATE_CN_RE.finditer(text))
            if matches:
                last = matches[-1]
                value = _date_value(last)
                if value:
                    values["effectiveAt"] = value
                    evidence["effectiveAt"] = _evidence(
                        value, "rule", "last_date_fallback", "rawText", last.group(0)
                    )

    @staticmethod
    def _source_document(document: ParsedDocument) -> str:
        filename = str((document.extras or {}).get("filename") or "").strip()
        return filename or document.title or document.doc_id

    @staticmethod
    def _apply_llm(
        document: ParsedDocument,
        extractor: LlmMetadataExtractor,
        missing: list[str],
        values: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        prompt = f"{document.title}\n{document.raw_text[:2000]}\nmissing={','.join(missing)}"
        prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        try:
            result = extractor(document, list(missing)) or {}
        except Exception as exc:  # LLM 不可用时保持规则结果，不影响主流程
            evidence["__llm_error__"] = {
                "extractor": "llm",
                "error": f"{type(exc).__name__}: {exc}",
                "promptDigest": prompt_digest,
            }
            return
        model = str(result.get("__model__") or result.get("model") or "unknown")
        response_digest = hashlib.sha256(
            json.dumps({k: v for k, v in result.items() if not k.startswith("__")}, sort_keys=True,
                       ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()
        for key in missing:
            value = result.get(key)
            if value in (None, ""):
                continue
            if key in ("effectiveAt", "issuedAt"):
                value = _normalize_date(str(value))
            values[key] = value
            item = _evidence(str(value), "llm", "llm_fallback", "llm", str(value))
            item.update({"model": model, "promptDigest": prompt_digest, "responseDigest": response_digest})
            evidence[key] = item


# --------------------------------------------------------------------------- 工具函数


def annotate_document(
    document: ParsedDocument, llm: LlmMetadataExtractor | None = None
) -> dict[str, Any]:
    """无状态便捷入口：按规则优先 + LLM 兜底抽取六要素（不落库）。"""
    return AnnotationService(llm=llm).annotate(document)


def evaluate_accuracy(
    cases: Iterable[tuple[ParsedDocument, Mapping[str, Any]]],
    llm: LlmMetadataExtractor | None = None,
) -> dict[str, Any]:
    """评估标注准确率：逐字段对比期望值，返回字段级与整体准确率。"""
    service = AnnotationService(InMemoryAnnotationStore(), llm=llm)
    total = 0
    correct = 0
    per_field: dict[str, list[int]] = {key: [0, 0] for key in REQUIRED_FIELDS}
    for document, expected in cases:
        meta = service.annotate(document)
        for key in REQUIRED_FIELDS:
            if key not in expected:
                continue
            per_field[key][1] += 1
            total += 1
            if _same_field(key, meta.get(key), expected[key]):
                per_field[key][0] += 1
                correct += 1
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 1.0,
        "perField": {
            key: round(value[0] / value[1], 4) if value[1] else None
            for key, value in per_field.items()
        },
    }


def _same_field(field_name: str, actual: Any, expected: Any) -> bool:
    if field_name == "standardNo":
        return _norm_standard_no(str(actual or "")) == _norm_standard_no(str(expected or ""))
    if field_name in ("effectiveAt", "issuedAt"):
        return _as_date(actual) == _as_date(expected)
    return str(actual or "").strip() == str(expected or "").strip()


def _candidate_texts(document: ParsedDocument) -> list[tuple[str, str]]:
    candidates = [("title", document.title or "")]
    filename = str((document.extras or {}).get("filename") or "")
    if filename:
        candidates.append(("filename", filename))
    candidates.append(("rawText", (document.raw_text or "")[:3000]))
    return candidates


def _find_labelled_date(text: str, keys: tuple[str, ...]) -> tuple[str, str] | None:
    for key in keys:
        pattern = re.compile(re.escape(key) + r"[^\n。]{0,20}?(\d{4}\s*[-年/.]\s*\d{1,2}\s*[-月/.]\s*\d{1,2}\s*日?)")
        match = pattern.search(text)
        if match:
            value = _normalize_date(match.group(1))
            if value:
                return value, match.group(0)
    return None


def _date_value(match: re.Match[str]) -> str | None:
    try:
        day = date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
    except ValueError:
        return None
    return day.isoformat()


def _normalize_date(value: str) -> str | None:
    match = _DATE_CN_RE.search(value or "")
    return _date_value(match) if match else None


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return _normalize_date(text) and date.fromisoformat(_normalize_date(text))  # type: ignore[arg-type]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _norm_standard_no(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip().upper()
    return text.replace("—", "-").replace("–", "-").replace("：", ":")


def _evidence(
    value: Any, extractor: str, rule: str | None, location: str, snippet: str
) -> dict[str, Any]:
    return {
        "value": value,
        "extractor": extractor,
        "rule": rule,
        "location": location,
        "snippet": (snippet or "")[:200],
    }


def _confidence(values: Mapping[str, Any], evidence: Mapping[str, Any]) -> float:
    weights = {"rule": 1.0, "llm": 0.75, "manual": 1.0, "none": 0.0}
    scores = []
    for key in REQUIRED_FIELDS:
        if not values.get(key):
            scores.append(0.0)
            continue
        extractor = (evidence.get(key) or {}).get("extractor", "none")
        scores.append(weights.get(extractor, 0.5))
    return round(sum(scores) / len(scores), 3)


def _validate_field(key: str, value: Any) -> Any:
    if key == "confidence":
        number = float(value)
        if not 0 <= number <= 1:
            raise ValueError("confidence 必须在 0..1 之间")
        return number
    if key in ("effectiveAt", "issuedAt"):
        if value in (None, ""):
            return None
        normalized = _normalize_date(str(value))
        if normalized is None:
            raise ValueError(f"{key} 不是合法日期：{value!r}")
        return normalized
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} 必须是字符串")
    return value


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value or "unknown")[:128]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
