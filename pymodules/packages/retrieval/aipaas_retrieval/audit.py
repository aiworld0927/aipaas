"""检索审计埋点：检索行为与越权尝试。

动作与 ``aipaas-openapi-v1.yaml`` 的 ``x-audit`` 对齐：
``retrieval.query``（成功检索）与 ``permission.denied``（越权尝试/行级过滤命中）。
审计写入失败不会中断检索，只记录日志，避免可用性依赖审计存储。
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

ACTION_QUERY = "retrieval.query"
ACTION_DENIED = "permission.denied"

logger = logging.getLogger(__name__)


class AuditSink(Protocol):
    def record(self, entry: dict[str, Any]) -> None: ...

    def entries(self) -> list[dict[str, Any]]: ...


class InMemoryAuditSink:
    """进程内审计（测试与单机部署）；条目只增不改。"""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            record = dict(entry)
            record["seq"] = len(self._entries) + 1
            self._entries.append(record)

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return [json.loads(json.dumps(entry, ensure_ascii=False)) for entry in self._entries]


class JsonlAuditSink:
    """``audit.jsonl`` 追加写：跨进程可回看，重启不丢。"""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self.directory / "retrieval-audit.jsonl"

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            seq = sum(1 for _ in self.path.open("r", encoding="utf-8")) + 1 if self.path.exists() else 1
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({**entry, "seq": seq}, ensure_ascii=False) + "\n")

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def audit_entry(
    action: str,
    *,
    user_id: str,
    tenant_id: str,
    allowed: bool,
    query: str = "",
    kb_id: str | None = None,
    reason: str | None = None,
    trace_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造审计条目；查询内容截断到 200 字符，避免审计中沉淀超长文本。"""
    return {
        "action": action,
        "userId": user_id,
        "tenantId": tenant_id,
        "kbId": kb_id,
        "allowed": allowed,
        "reason": reason,
        "traceId": trace_id,
        "query": (query or "")[:200],
        "detail": detail or {},
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


def safe_record(sink: AuditSink | None, entry: dict[str, Any]) -> None:
    if sink is None:
        return
    try:
        sink.record(entry)
    except Exception:  # pragma: no cover - 审计不可用不应影响检索
        logger.warning("检索审计写入失败", exc_info=True)
