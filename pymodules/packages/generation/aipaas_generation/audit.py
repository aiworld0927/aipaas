"""生成链路审计埋点：问答、输入拦截、注入拦截与模型调用。

动作与 ``aipaas-openapi-v1.yaml`` 的 ``x-audit`` 对齐：
``chat.query``、``security.input.blocked``、``security.prompt_injection.blocked``、
``model.invoke``。审计只记录长度、哈希、计数等元信息，禁止写入完整 Prompt 或答案正文。
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

ACTION_CHAT_QUERY = "chat.query"
ACTION_INPUT_BLOCKED = "security.input.blocked"
ACTION_INJECTION_BLOCKED = "security.prompt_injection.blocked"
ACTION_MODEL_INVOKE = "model.invoke"

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
    """``generation-audit.jsonl`` 追加写：跨进程可回看，重启不丢。"""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self.directory / "generation-audit.jsonl"

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            seq = 0
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as handle:
                    seq = sum(1 for _ in handle)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({**entry, "seq": seq + 1}, ensure_ascii=False) + "\n")

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
    allowed: bool = True,
    reason: str | None = None,
    trace_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造审计条目；``detail`` 由调用方保证不含完整 Prompt / 答案正文。"""
    return {
        "action": action,
        "userId": user_id,
        "tenantId": tenant_id,
        "allowed": allowed,
        "reason": reason,
        "traceId": trace_id,
        "detail": detail or {},
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


def safe_record(sink: AuditSink | None, entry: dict[str, Any]) -> None:
    """审计写入失败不阻断生成，只记录日志。"""
    if sink is None:
        return
    try:
        sink.record(entry)
    except Exception:  # pragma: no cover - 审计不可用不应影响生成
        logger.warning("生成链路审计写入失败", exc_info=True)
