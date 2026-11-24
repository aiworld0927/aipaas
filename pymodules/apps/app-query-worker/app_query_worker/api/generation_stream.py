"""#113 问答 SSE 传输层：事件缓冲、断线续传与心跳。

设计要点：
- 生产者线程执行 ``GenerationService.stream_answer``，事件写入有界缓冲；
- 每个事件有单调递增序号，SSE ``id`` 形如 ``<streamKey>:<seq>``，
  客户端断线后回传 ``Last-Event-ID`` 即可从该序号后继续，不重复已生成内容；
- 缓冲有 TTL 与容量上限，P0 单实例内存实现（多实例续传归 P1）；
- 流建立后即使客户端断开，生成也会继续跑完，保证最终结果完整。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterator

logger = logging.getLogger(__name__)

SSE_EVENTS = ("meta", "citation", "delta", "done", "error", "ping")
DEFAULT_TTL_SECONDS = 300.0
DEFAULT_MAX_STREAMS = 200
TERMINAL_EVENTS = {"done", "error"}


@dataclass(slots=True)
class StreamEventRecord:
    seq: int
    event: str
    data: dict


@dataclass(slots=True)
class StreamBuffer:
    """单次问答的事件缓冲；``condition`` 驱动跨线程的等待/唤醒。"""

    key: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    events: list[StreamEventRecord] = field(default_factory=list)
    finished: bool = False
    created_at: float = field(default_factory=time.monotonic)
    last_access: float = field(default_factory=time.monotonic)
    condition: threading.Condition = field(default_factory=threading.Condition)

    def wait(self, cursor: int, timeout: float) -> tuple[list[StreamEventRecord], bool]:
        """返回 ``cursor`` 之后的事件；无新事件时最多等待 ``timeout`` 秒。"""
        deadline = time.monotonic() + timeout
        with self.condition:
            while not self.finished and not any(item.seq > cursor for item in self.events):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            self.last_access = time.monotonic()
            pending = [item for item in self.events if item.seq > cursor]
            return pending, self.finished


class StreamRegistry:
    """进程内流注册表：按 ``streamKey`` 查找，支持 Last-Event-ID 续传。"""

    def __init__(self, *, max_streams: int = DEFAULT_MAX_STREAMS, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self.max_streams = max_streams
        self.ttl_seconds = ttl_seconds
        self._buffers: dict[str, StreamBuffer] = {}
        self._lock = threading.RLock()

    def start(self, factory: Callable[[], Iterator[tuple[str, dict]]]) -> StreamBuffer:
        """创建缓冲并在后台线程消费生成事件。"""
        buffer = StreamBuffer()
        with self._lock:
            self._evict()
            self._buffers[buffer.key] = buffer
        thread = threading.Thread(
            target=self._run, args=(buffer, factory), name=f"qa-stream-{buffer.key}", daemon=True
        )
        thread.start()
        return buffer

    def get(self, key: str) -> StreamBuffer | None:
        with self._lock:
            buffer = self._buffers.get(key)
            if buffer is not None:
                buffer.last_access = time.monotonic()
            return buffer

    def _run(self, buffer: StreamBuffer, factory: Callable[[], Iterator[tuple[str, dict]]]) -> None:
        seq = 0
        try:
            for event, payload in factory():
                seq += 1
                data = dict(payload)
                if event == "delta":
                    # 契约要求 delta.seq 与 SSE 序号一致（用于 Last-Event-ID 续传）。
                    data.setdefault("seq", seq)
                with buffer.condition:
                    buffer.events.append(StreamEventRecord(seq, event, data))
                    buffer.condition.notify_all()
                if event in TERMINAL_EVENTS:
                    break
        except Exception:
            logger.exception("问答流式生成失败 stream=%s", buffer.key)
            with buffer.condition:
                seq += 1
                buffer.events.append(
                    StreamEventRecord(
                        seq,
                        "error",
                        {
                            "code": "SYS.INTERNAL_ERROR",
                            "message": "问答生成失败，请稍后重试",
                            "retryable": True,
                        },
                    )
                )
                buffer.condition.notify_all()
        finally:
            with buffer.condition:
                buffer.finished = True
                buffer.condition.notify_all()

    def _evict(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, buffer in self._buffers.items()
            if buffer.finished and now - buffer.last_access > self.ttl_seconds
        ]
        for key in expired:
            self._buffers.pop(key, None)
        while len(self._buffers) >= self.max_streams:
            oldest = min(self._buffers.values(), key=lambda item: item.last_access)
            self._buffers.pop(oldest.key, None)


def parse_last_event_id(value: str | None) -> tuple[str, int]:
    """解析 ``<streamKey>:<seq>``；非法值按“从头开始”处理。"""
    if not value:
        return "", 0
    key, _, seq_text = value.rpartition(":")
    if not key:
        return "", 0
    try:
        return key, max(0, int(seq_text))
    except ValueError:
        return "", 0


def format_sse(record: StreamEventRecord, stream_key: str) -> str:
    payload = json.dumps(record.data, ensure_ascii=False)
    return f"id: {stream_key}:{record.seq}\nevent: {record.event}\ndata: {payload}\n\n"


def format_ping() -> str:
    return f"event: ping\ndata: {json.dumps({'at': time.time()})}\n\n"
