"""#405 标准简介：从条目化内容抽取五要素，缺失标注“未识别”。

设计原则：
- **抽取优先、不编造**：编号/名称/实施日期来自已抽取的标准元数据与原文证据；
  适用范围与主要技术内容只由原文条款标题/正文拼接，不做生成式补全。
- **可核对、可追溯**：每个要素记录 ``sourceClauseNo`` / ``sourceQuote``，
  顶层记录文档版本、生成时间、抽取器版本与条款数快照。
- **版本化**：每次重新生成都会新增版本，``latest`` 指向最新；历史版本不覆盖。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from aipaas_pipeline.models import Clause, ParsedDocument
from aipaas_pipeline.service import ParseService

MISSING = "未识别"
SUMMARY_EXTRACTOR_VERSION = "summary-extract-v1"
SUMMARY_MODEL = "extractive-v1"

_STANDARD_NO_RE = re.compile(
    r"(?:GB/T|GB/Z|GB|JR/T|JR|YB/T|DB\d*/T|DB\d*|SJ/T|YD/T|ISO/IEC|ISO|IEC)"
    r"\s*\d+(?:\.\d+)*\s*[-—–:：]\s*\d{4}",
    re.IGNORECASE,
)
_SCOPE_SENTENCE_RE = re.compile(
    r"[^。；\n]*(?:本文件|本标准|本规范|本指南|本部分)[^。；\n]*(?:规定|适用于|界定了|描述了|提出了)[^。；\n]*[。；]?"
)
_EFFECTIVE_RE = re.compile(
    r"(?:实施日期|实施时间|自|于)\s*[:：]?\s*"
    r"(?P<year>\d{4})\s*[-年/.]\s*(?P<month>\d{1,2})\s*[-月/.]\s*(?P<day>\d{1,2})\s*日?"
)


class SummaryError(Exception):
    """简介生成可预期错误基类。"""


class SummaryNotFound(SummaryError):
    """文档不存在、解析未成功或尚无简介。"""


class SummaryForbidden(SummaryError):
    """租户不匹配，禁止读取/生成。"""


class SummaryStore(Protocol):
    def latest(self, document_id: str) -> dict[str, Any] | None: ...

    def save(self, payload: dict[str, Any]) -> None: ...

    def list_versions(self, document_id: str) -> list[int]: ...

    def append_audit(self, entry: dict[str, Any]) -> None: ...


class InMemorySummaryStore:
    """测试用内存实现；返回深拷贝避免调用方修改内部状态。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[int, dict[str, Any]]] = {}
        self._audit: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def latest(self, document_id: str) -> dict[str, Any] | None:
        with self._lock:
            versions = self._data.get(document_id) or {}
            if not versions:
                return None
            return json.loads(json.dumps(versions[max(versions)], ensure_ascii=False))

    def save(self, payload: dict[str, Any]) -> None:
        with self._lock:
            document_id = payload["documentId"]
            version = int(payload["summaryVersion"])
            self._data.setdefault(document_id, {})[version] = json.loads(
                json.dumps(payload, ensure_ascii=False)
            )

    def list_versions(self, document_id: str) -> list[int]:
        with self._lock:
            return sorted(self._data.get(document_id, {}))

    def append_audit(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._audit.append(json.loads(json.dumps(entry, ensure_ascii=False)))


class FileSummaryStore:
    """``<root>/<docId>/v{n}.json`` + ``latest.json`` + ``audit.jsonl``（只增不改）。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()

    def latest(self, document_id: str) -> dict[str, Any] | None:
        pointer = self.root / _safe(document_id) / "latest.json"
        if not pointer.exists():
            return None
        version = int(json.loads(pointer.read_text(encoding="utf-8"))["version"])
        return self._read(document_id, version)

    def save(self, payload: dict[str, Any]) -> None:
        with self._lock:
            document_id = payload["documentId"]
            version = int(payload["summaryVersion"])
            directory = self.root / _safe(document_id)
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"v{version}.json"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(target)
            pointer = directory / "latest.json"
            pointer_tmp = pointer.with_suffix(".tmp")
            pointer_tmp.write_text(
                json.dumps({"version": version, "summaryId": payload["id"]}, ensure_ascii=False),
                encoding="utf-8",
            )
            pointer_tmp.replace(pointer)

    def list_versions(self, document_id: str) -> list[int]:
        directory = self.root / _safe(document_id)
        if not directory.exists():
            return []
        versions = []
        for path in directory.glob("v*.json"):
            match = re.fullmatch(r"v(\d+)\.json", path.name)
            if match:
                versions.append(int(match.group(1)))
        return sorted(versions)

    def append_audit(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / "audit.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _read(self, document_id: str, version: int) -> dict[str, Any] | None:
        path = self.root / _safe(document_id) / f"v{version}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


@dataclass(slots=True)
class _Element:
    key: str
    label: str
    value: str
    source_clause_no: str | None = None
    source_quote: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"key": self.key, "label": self.label, "value": self.value}
        if self.source_clause_no:
            payload["sourceClauseNo"] = self.source_clause_no
        if self.source_quote:
            payload["sourceQuote"] = self.source_quote
        return payload


class SummaryService:
    """标准简介生成与查询。依赖解析产物（条款 + 元数据证据）。"""

    def __init__(self, parse_service: ParseService, store: SummaryStore | None = None) -> None:
        self.parse_service = parse_service
        self.store = store or InMemorySummaryStore()

    # ------------------------------------------------------------------ 公开 API

    def latest(self, document_id: str, *, tenant_id: str | None = None) -> dict[str, Any] | None:
        document = self._document(document_id, tenant_id)
        return self.store.latest(document.doc_id)

    def generate(
        self,
        document_id: str,
        *,
        tenant_id: str | None = None,
        regenerate: bool = False,
        actor: str = "system",
    ) -> dict[str, Any]:
        document = self._document(document_id, tenant_id)
        existing = self.store.latest(document.doc_id)
        if existing is not None and not regenerate:
            return existing
        metadata = self.parse_service.get_metadata(document.doc_id) or {}
        payload = self._build(document, metadata)
        versions = self.store.list_versions(document.doc_id)
        payload["summaryVersion"] = (versions[-1] + 1) if versions else 1
        payload["id"] = "sum_" + uuid.uuid4().hex
        self.store.save(payload)
        self.store.append_audit(
            {
                "at": payload["generatedAt"],
                "documentId": document.doc_id,
                "tenantId": payload["tenantId"],
                "action": "summary.generate",
                "actor": actor,
                "version": payload["summaryVersion"],
                "documentVersion": payload["documentVersion"],
                "extractor": SUMMARY_EXTRACTOR_VERSION,
                "missing": [item["key"] for item in payload["elements"] if item["value"] == MISSING],
            }
        )
        return payload

    def versions(self, document_id: str) -> list[int]:
        return self.store.list_versions(document_id)

    # ------------------------------------------------------------------ 构建

    def _document(self, document_id: str, tenant_id: str | None) -> ParsedDocument:
        document = self.parse_service.get_document(document_id)
        if document is None or not document.success:
            raise SummaryNotFound(f"文档不存在或尚未成功解析：{document_id}")
        if tenant_id and document.tenant_id and tenant_id != document.tenant_id:
            raise SummaryForbidden("跨租户访问被拒绝")
        return document

    def _build(self, document: ParsedDocument, metadata: dict[str, Any]) -> dict[str, Any]:
        evidence = metadata.get("evidence") or {}
        clauses = document.clauses
        standard_no = self._standard_no(document, metadata, evidence)
        standard_name = self._standard_name(document, metadata, evidence)
        scope = self._scope(clauses)
        main_content = self._main_content(document, clauses)
        effective = self._effective_date(document, metadata, evidence)
        elements = [standard_no, standard_name, scope, main_content, effective]
        identified = sum(1 for item in elements if item.value != MISSING)
        effective_at = effective.value if effective.value != MISSING else None
        return {
            "tenantId": document.tenant_id or metadata.get("tenantId") or "default",
            "documentId": document.doc_id,
            "standardNo": "" if standard_no.value == MISSING else standard_no.value,
            "standardName": "" if standard_name.value == MISSING else standard_name.value,
            "scope": scope.value,
            "mainContent": main_content.value,
            "effectiveAt": effective_at,
            "elements": [item.to_dict() for item in elements],
            "generatedAt": _now(),
            "model": SUMMARY_MODEL,
            "promptTemplateVersion": SUMMARY_EXTRACTOR_VERSION,
            "confidence": round(identified / len(elements), 2),
            "documentVersion": document.version,
            "clauseCount": len(clauses),
            "source": "EXTRACTIVE",
            "trace": {
                "documentVersion": document.version,
                "metadataSource": metadata.get("source"),
                "metadataConfidence": metadata.get("confidence"),
                "missingElements": [item.key for item in elements if item.value == MISSING],
            },
        }

    @staticmethod
    def _standard_no(
        document: ParsedDocument, metadata: dict[str, Any], evidence: dict[str, Any]
    ) -> _Element:
        value = str(metadata.get("standardNo") or document.standard_no or "").strip()
        quote = _evidence_quote(evidence.get("standardNo")) or value
        if not value:
            match = _STANDARD_NO_RE.search(document.raw_text or "") or _STANDARD_NO_RE.search(document.title or "")
            value = match.group(0) if match else ""
            quote = match.group(0) if match else quote
        return _Element("standard_no", "标准编号", value or MISSING, None, _clip(quote))

    @staticmethod
    def _standard_name(
        document: ParsedDocument, metadata: dict[str, Any], evidence: dict[str, Any]
    ) -> _Element:
        value = str(metadata.get("standardName") or "").strip()
        quote = _evidence_quote(evidence.get("standardName")) or value
        if not value:
            value = _STANDARD_NO_RE.sub("", document.title or "").strip(" -—–:：")
            quote = quote or document.title
        return _Element("standard_name", "标准名称", value or MISSING, None, _clip(quote))

    @staticmethod
    def _scope(clauses: list[Clause]) -> _Element:
        preferred = _clause_by_title(clauses, ("范围",))
        candidates = ([preferred] if preferred else []) + [
            clause for clause in clauses if clause is not preferred
        ]
        for clause in candidates:
            if not clause.text:
                continue
            match = _SCOPE_SENTENCE_RE.search(clause.text)
            text = match.group(0).strip() if match else ""
            if not text and "范围" in (clause.title or ""):
                text = clause.text.strip()
            if text:
                return _Element(
                    "scope",
                    "适用范围",
                    _clip(text, 500),
                    clause.clause_no or None,
                    _clip(text, 300),
                )
        return _Element("scope", "适用范围", MISSING)

    @staticmethod
    def _main_content(document: ParsedDocument, clauses: list[Clause]) -> _Element:
        if not clauses:
            return _Element("main_content", "主要技术内容概述", MISSING)
        titles: list[str] = []
        for clause in clauses:
            title = (clause.title or "").strip()
            if not title or title in titles:
                continue
            titles.append(title)
            if len(titles) >= 8:
                break
        if not titles:
            for node in document.chapter_tree:
                if node.title and node.title not in titles:
                    titles.append(node.title)
                if len(titles) >= 8:
                    break
        if not titles:
            return _Element("main_content", "主要技术内容概述", MISSING)
        value = f"本文件共 {len(clauses)} 条，主要技术内容包括：{'、'.join(titles)}。"
        return _Element(
            "main_content",
            "主要技术内容概述",
            _clip(value, 500),
            clauses[0].clause_no or None,
            _clip("；".join(titles), 300),
        )

    @staticmethod
    def _effective_date(
        document: ParsedDocument, metadata: dict[str, Any], evidence: dict[str, Any]
    ) -> _Element:
        value = str(metadata.get("effectiveAt") or "").strip()
        quote = _evidence_quote(evidence.get("effectiveAt"))
        if not value:
            match = _EFFECTIVE_RE.search(document.raw_text or "")
            if match:
                value = _date_value(match)
                quote = quote or match.group(0)
        if not value:
            return _Element("effective_date", "实施日期", MISSING)
        return _Element("effective_date", "实施日期", value, None, _clip(quote or value))


def build_default_summary_service(parse_service: ParseService) -> SummaryService:
    """使用与解析产物相同的数据根目录持久化简介。"""
    from aipaas_pipeline.service import default_local_root

    return SummaryService(parse_service, FileSummaryStore(default_local_root() / "summaries"))


def _clause_by_title(clauses: list[Clause], needles: tuple[str, ...]) -> Clause | None:
    for clause in clauses:
        if any(needle in (clause.title or "") for needle in needles):
            return clause
    return None


def _evidence_quote(evidence: Any) -> str:
    if isinstance(evidence, dict):
        return str(evidence.get("snippet") or evidence.get("value") or "")
    return ""


def _date_value(match: re.Match[str]) -> str:
    try:
        value = date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
        return value.isoformat()
    except ValueError:
        return ""


def _clip(value: str | None, limit: int = 500) -> str:
    text = (value or "").strip()
    return text if len(text) <= limit else text[:limit]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value or "unknown")[:128]


def summary_digest(payload: dict[str, Any]) -> str:
    """简介内容摘要，便于导出记录与审计核对。"""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
