"""解析结果版本仓库：记录版本、产物键与失败定位。

多副本部署时应替换为 PostgreSQL 实现；接口保持不变。
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class ParseRecord:
    document_id: str
    version: int
    parser: str
    success: bool
    artifact_key: str
    payload: dict[str, Any]
    parsed_at: datetime
    tenant_id: str = "default"
    failure_code: str | None = None
    pages: int = 0
    chunk_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "documentId": self.document_id,
            "tenantId": self.tenant_id,
            "version": self.version,
            "parser": self.parser,
            "success": self.success,
            "failureCode": self.failure_code,
            "pages": self.pages,
            "artifactKey": self.artifact_key,
            "chunkCount": self.chunk_count,
            "parsedAt": self.parsed_at.isoformat(),
            "payload": self.payload,
            **({"extra": self.extra} if self.extra else {}),
        }


class ParseResultRepository(Protocol):
    def next_version(self, document_id: str) -> int: ...

    def save(self, record: ParseRecord) -> None: ...

    def latest(self, document_id: str) -> ParseRecord | None: ...

    def list_versions(self, document_id: str) -> list[int]: ...

    def list_latest(self) -> list[ParseRecord]: ...


class InMemoryParseResultRepository:
    def __init__(self) -> None:
        self._records: dict[str, dict[int, ParseRecord]] = {}
        self._lock = threading.RLock()

    def next_version(self, document_id: str) -> int:
        with self._lock:
            versions = self._records.get(document_id, {})
            return max(versions, default=0) + 1

    def save(self, record: ParseRecord) -> None:
        with self._lock:
            self._records.setdefault(record.document_id, {})[record.version] = record

    def latest(self, document_id: str) -> ParseRecord | None:
        with self._lock:
            versions = self._records.get(document_id, {})
            if not versions:
                return None
            return versions[max(versions)]

    def list_versions(self, document_id: str) -> list[int]:
        with self._lock:
            return sorted(self._records.get(document_id, {}))

    def list_latest(self) -> list[ParseRecord]:
        with self._lock:
            return [versions[max(versions)] for versions in self._records.values() if versions]


class FileParseResultRepository:
    """``<root>/<document_id>/v{n}.json`` 持久化，版本号按目录内最大值递增。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()

    def next_version(self, document_id: str) -> int:
        with self._lock:
            versions = self.list_versions(document_id)
            return (max(versions) if versions else 0) + 1

    def save(self, record: ParseRecord) -> None:
        with self._lock:
            directory = self.root / _safe(record.document_id)
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"v{record.version}.json"
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(target)

    def latest(self, document_id: str) -> ParseRecord | None:
        with self._lock:
            versions = self.list_versions(document_id)
            if not versions:
                return None
            return self._read(document_id, versions[-1])

    def list_versions(self, document_id: str) -> list[int]:
        with self._lock:
            directory = self.root / _safe(document_id)
            if not directory.exists():
                return []
            versions = []
            for path in directory.glob("v*.json"):
                match = re.fullmatch(r"v(\d+)\.json", path.name)
                if match:
                    versions.append(int(match.group(1)))
            return sorted(versions)

    def list_latest(self) -> list[ParseRecord]:
        if not self.root.exists():
            return []
        records = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir():
                continue
            latest = self.latest(directory.name)
            if latest is not None:
                records.append(latest)
        return records

    def _read(self, document_id: str, version: int) -> ParseRecord | None:
        target = self.root / _safe(document_id) / f"v{version}.json"
        if not target.exists():
            return None
        data = json.loads(target.read_text(encoding="utf-8"))
        return ParseRecord(
            document_id=data["documentId"],
            version=data["version"],
            parser=data["parser"],
            success=data["success"],
            artifact_key=data["artifactKey"],
            payload=data["payload"],
            parsed_at=datetime.fromisoformat(data["parsedAt"]),
            tenant_id=data.get("tenantId", "default"),
            failure_code=data.get("failureCode"),
            pages=data.get("pages", 0),
            chunk_count=data.get("chunkCount", 0),
            extra=data.get("extra", {}),
        )


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)[:128]
