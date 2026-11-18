"""可观测基础设施：#173 结构化请求日志、#175 全链路 Trace、#177 Prometheus 指标。

设计约束：
- 日志、Trace、指标共享同一个 ``X-Trace-Id``；同时兼容 W3C ``traceparent``，
  Java 网关与 Python 服务之间可继续传递同一个 Trace。
- 请求日志只记录方法、路径（不含查询串）、状态码、耗时与匿名化 IP，
  不记录请求体、响应体、Token、密码等敏感内容。
- Trace 采用 OpenTelemetry 数据模型（traceId/spanId/parentSpanId/status/attributes），
  进程内有界存储，供 ``/api/v1/observability/traces/{traceId}`` 按 TraceID 查询。
- 指标使用 Prometheus 文本格式，不依赖 prometheus_client；HTTP 中间件自动记录
  QPS、状态码与耗时直方图，业务侧记录检索命中与拒答。
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import threading
import time
import uuid
from collections import Counter as _Counter
from datetime import datetime, timezone
from typing import Any, Iterator

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from aipaas_common.core.log import logger

TRACE_HEADER = "X-Trace-Id"
REQUEST_ID_HEADER = "X-Request-Id"
TRACEPARENT_HEADER = "traceparent"

_MISSING = object()

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
span_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("span_id", default="")
_trace_flags_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_flags", default="01")


# ---------------------------------------------------------------------------
# ID / traceparent
# ---------------------------------------------------------------------------
def _hex_id(size: int) -> str:
    return uuid.uuid4().hex[:size]


def _otel_trace_id(value: str) -> str:
    """把任意 TraceID 归一为 32 位十六进制，保证 traceparent 合法且可逆查询。"""
    candidate = (value or "").lower()
    if len(candidate) == 32 and all(ch in "0123456789abcdef" for ch in candidate):
        return candidate
    return hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:32]


def _otel_span_id(value: str) -> str:
    candidate = (value or "").lower()
    if len(candidate) == 16 and all(ch in "0123456789abcdef" for ch in candidate):
        return candidate
    return hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16]


def parse_traceparent(value: str | None) -> tuple[str, str, str] | None:
    """解析 W3C traceparent，返回 ``(trace_id, parent_span_id, flags)``。"""
    if not value:
        return None
    parts = value.strip().split("-")
    if len(parts) != 4:
        return None
    version, trace_id, span_id, flags = parts
    if len(version) != 2 or len(trace_id) != 32 or len(span_id) != 16:
        return None
    if not all(ch in "0123456789abcdef" for ch in (trace_id + span_id)):
        return None
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return trace_id, span_id, flags or "01"


def format_traceparent(trace_id: str, span_id: str, flags: str = "01") -> str:
    return f"00-{_otel_trace_id(trace_id)}-{_otel_span_id(span_id)}-{flags}"


def current_trace_id() -> str:
    return trace_id_var.get()


def current_span_id() -> str:
    return span_id_var.get()


# ---------------------------------------------------------------------------
# Span / TraceStore
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Span:
    """OpenTelemetry 兼容的 Span 快照。"""

    def __init__(
        self,
        *,
        name: str,
        service: str,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None,
        kind: str = "INTERNAL",
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.service = service
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.kind = kind
        self.attributes: dict[str, Any] = dict(attributes or {})
        self.start_time = _now_iso()
        self.end_time: str | None = None
        self.duration_ms: float = 0.0
        self.status = "UNSET"
        self.error: str | None = None

    def attribute(self, key: str, value: Any) -> "Span":
        if key and value is not None:
            self.attributes[key] = value
        return self

    def finish(self, duration_ms: float, error: BaseException | None = None) -> None:
        self.end_time = _now_iso()
        self.duration_ms = round(duration_ms, 3)
        if error is not None:
            self.status = "ERROR"
            self.attributes["error.type"] = type(error).__name__
            # 只记录异常类型与消息，不记录请求/响应内容。
            self.error = str(error)[:256]
        elif self.status == "UNSET":
            self.status = "OK"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "name": self.name,
            "service": self.service,
            "kind": self.kind,
            "startTime": self.start_time,
            "endTime": self.end_time,
            "durationMs": self.duration_ms,
            "status": self.status,
            "attributes": self.attributes,
        }
        if self.parent_span_id:
            payload["parentSpanId"] = self.parent_span_id
        if self.error:
            payload["error"] = self.error
        return payload


class TraceStore:
    """有界内存 Span 存储；按 traceId 聚合，淘汰最旧 Span。"""

    def __init__(self, max_spans: int = 5000) -> None:
        self._max_spans = max_spans
        self._spans: list[Span] = []
        self._lock = threading.Lock()

    def record(self, span: Span) -> None:
        with self._lock:
            self._spans.append(span)
            if len(self._spans) > self._max_spans:
                del self._spans[: len(self._spans) - self._max_spans]

    def get(self, trace_id: str) -> list[dict[str, Any]]:
        wanted = _otel_trace_id(trace_id)
        with self._lock:
            spans = [
                span
                for span in self._spans
                if _otel_trace_id(span.trace_id) == wanted
            ]
        spans.sort(key=lambda item: (item.start_time, item.span_id))
        return [span.to_dict() for span in spans]

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()


trace_store = TraceStore()
_service_name_var: contextvars.ContextVar[str] = contextvars.ContextVar("service_name", default="aipaas")


@contextlib.contextmanager
def span(name: str, /, **attributes: Any) -> Iterator[Span]:
    """创建并记录一个 Span；异常自动标记 ``status=ERROR`` 并继续抛出。"""
    trace_id = current_trace_id() or f"tr_{uuid.uuid4().hex}"
    parent = current_span_id() or None
    current = Span(
        name=name,
        service=_service_name_var.get(),
        trace_id=trace_id,
        span_id=_hex_id(16),
        parent_span_id=parent,
        attributes=attributes,
    )
    token_trace = trace_id_var.set(trace_id)
    token_span = span_id_var.set(current.span_id)
    started = time.perf_counter()
    try:
        yield current
    except BaseException as exc:
        current.finish((time.perf_counter() - started) * 1000, exc)
        trace_store.record(current)
        raise
    else:
        current.finish((time.perf_counter() - started) * 1000)
        trace_store.record(current)
    finally:
        span_id_var.reset(token_span)
        trace_id_var.reset(token_trace)


def record_span(span_obj: Span) -> None:
    """供异步/手动埋点使用。"""
    trace_store.record(span_obj)


def trace_snapshot(trace_id: str) -> dict[str, Any]:
    spans = trace_store.get(trace_id)
    return {
        "traceId": trace_id,
        "spanCount": len(spans),
        "spans": spans,
    }


# ---------------------------------------------------------------------------
# 结构化请求日志
# ---------------------------------------------------------------------------
def _anon_ip(value: str | None) -> str:
    if not value:
        return ""
    ip = value.split(",")[0].strip()
    if "." in ip:
        parts = ip.split(".")
        return ".".join(parts[:-1] + ["0"]) if len(parts) == 4 else ip
    if ":" in ip:
        return ":".join(ip.split(":")[:4]) + "::"
    return ip


def _log_request(
    *,
    service: str,
    trace_id: str,
    method: str,
    path: str,
    status: int,
    duration_ms: float,
    user_id: str,
    tenant_id: str,
    ip: str,
    outcome: str,
) -> None:
    entry = {
        "ts": _now_iso(),
        "level": "INFO" if status < 500 else "ERROR",
        "kind": "request",
        "service": service,
        "traceId": trace_id,
        "userId": user_id,
        "tenantId": tenant_id,
        "method": method,
        "path": path,
        "status": status,
        "durationMs": round(duration_ms, 3),
        "outcome": outcome,
        "clientIp": _anon_ip(ip),
    }
    logger.info(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))


# ---------------------------------------------------------------------------
# Prometheus 指标
# ---------------------------------------------------------------------------
def _escape_label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _render_labels(labels: tuple[str, ...], key: tuple[str, ...]) -> str:
    if not labels:
        return ""
    pairs = ",".join(f'{name}="{_escape_label(value)}"' for name, value in zip(labels, key))
    return "{" + pairs + "}"


def _labels_with_le(base: str, bound: float | str) -> str:
    le = f'le="{bound}"'
    return "{" + le + "}" if not base else base[:-1] + "," + le + "}"


class _Metric:
    def __init__(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help_text = help_text
        self.labels = labels
        self._lock = threading.Lock()

    def _key(self, label_values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(str(item) for item in label_values)


class Counter(_Metric):
    def __init__(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        super().__init__(name, help_text, labels)
        self._values: dict[tuple[str, ...], float] = {}

    def inc(self, amount: float = 1.0, **label_values: str) -> None:
        key = self._key(tuple(label_values.get(name, "") for name in self.labels))
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} counter"]
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            lines.append(f"{self.name}{_render_labels(self.labels, key)} {value:g}")
        return lines


class Gauge(_Metric):
    def __init__(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        super().__init__(name, help_text, labels)
        self._values: dict[tuple[str, ...], float] = {}

    def set(self, value: float, **label_values: str) -> None:
        key = self._key(tuple(label_values.get(name, "") for name in self.labels))
        with self._lock:
            self._values[key] = value

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} gauge"]
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            lines.append(f"{self.name}{_render_labels(self.labels, key)} {value:g}")
        return lines


class Histogram(_Metric):
    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(
        self,
        name: str,
        help_text: str,
        labels: tuple[str, ...] = (),
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, help_text, labels)
        self.buckets = tuple(sorted(buckets))
        self._values: dict[tuple[str, ...], list[float]] = {}

    def observe(self, value_seconds: float, **label_values: str) -> None:
        key = self._key(tuple(label_values.get(name, "") for name in self.labels))
        with self._lock:
            data = self._values.setdefault(key, [0.0] * (len(self.buckets) + 2))
            data[-2] += value_seconds
            data[-1] += 1
            for index, bound in enumerate(self.buckets):
                if value_seconds <= bound:
                    data[index] += 1

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} histogram"]
        with self._lock:
            snapshot = list(self._values.items())
        for key, value in sorted(snapshot):
            base = _render_labels(self.labels, key)
            for index, bound in enumerate(self.buckets):
                lines.append(f"{self.name}_bucket{_labels_with_le(base, bound)} {value[index]:g}")
            lines.append(f"{self.name}_bucket{_labels_with_le(base, '+Inf')} {value[-1]:g}")
            lines.append(f"{self.name}_sum{base} {value[-2]:g}")
            lines.append(f"{self.name}_count{base} {value[-1]:g}")
        return lines


class MetricsRegistry:
    def __init__(self) -> None:
        self.http_requests = Counter(
            "aipaas_http_requests_total", "HTTP requests by service/method/path/status",
            ("service", "method", "path", "status"),
        )
        self.http_duration = Histogram(
            "aipaas_http_request_duration_seconds", "HTTP request latency in seconds",
            ("service", "method", "path"),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
        )
        self.retrieval_queries = Counter(
            "aipaas_retrieval_queries_total", "Retrieval queries", ("service",),
        )
        self.retrieval_hits = Counter(
            "aipaas_retrieval_hits_total", "Retrieval queries returning at least one hit", ("service",),
        )
        self.retrieval_duration = Histogram(
            "aipaas_retrieval_duration_seconds", "Retrieval latency in seconds", ("service",),
        )
        self.generation_requests = Counter(
            "aipaas_generation_requests_total", "Generation requests", ("service",),
        )
        self.generation_refusals = Counter(
            "aipaas_generation_refusals_total", "Generation refusals", ("service", "reason"),
        )
        self.generation_duration = Histogram(
            "aipaas_generation_duration_seconds", "Generation latency in seconds", ("service",),
        )
        self.process_memory = Gauge(
            "aipaas_process_resident_memory_bytes", "Resident memory of the process",
            ("service",),
        )
        self.process_cpu = Gauge(
            "aipaas_process_cpu_seconds_total", "CPU seconds consumed by the process",
            ("service",),
        )

    def render(self, service: str) -> str:
        self._sample_process(service)
        lines: list[str] = []
        for metric in (
            self.http_requests, self.http_duration, self.retrieval_queries, self.retrieval_hits,
            self.retrieval_duration, self.generation_requests, self.generation_refusals,
            self.generation_duration, self.process_memory, self.process_cpu,
        ):
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"

    def _sample_process(self, service: str) -> None:
        try:
            with open("/proc/self/status", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        self.process_memory.set(float(line.split()[1]) * 1024, service=service)
                        break
        except OSError:
            pass
        try:
            with open("/proc/self/stat", encoding="utf-8") as handle:
                fields = handle.read().split()
            ticks = os.sysconf("SC_CLK_TCK")
            self.process_cpu.set((int(fields[13]) + int(fields[14])) / ticks, service=service)
        except (OSError, ValueError, IndexError):
            pass


metrics = MetricsRegistry()


def mark_retrieval(hit: bool, duration_seconds: float, service: str) -> None:
    metrics.retrieval_queries.inc(service=service)
    if hit:
        metrics.retrieval_hits.inc(service=service)
    metrics.retrieval_duration.observe(duration_seconds, service=service)


def mark_generation(refused: bool, duration_seconds: float, service: str, reason: str = "") -> None:
    metrics.generation_requests.inc(service=service)
    if refused:
        metrics.generation_refusals.inc(service=service, reason=reason or "UNKNOWN")
    metrics.generation_duration.observe(duration_seconds, service=service)


# ---------------------------------------------------------------------------
# FastAPI 集成
# ---------------------------------------------------------------------------
def install_observability(app: FastAPI, service: str, *, max_spans: int = 5000) -> None:
    """一次性安装 Trace 上下文、结构化请求日志、HTTP 指标与查询端点。"""
    _service_name_var.set(service)
    global trace_store
    if max_spans > 0:
        trace_store = TraceStore(max_spans=max_spans)

    @app.middleware("http")
    async def observability_middleware(request: Request, call_next):
        # 1) 延续上游 Trace：traceparent 优先，其次 X-Trace-Id / X-Request-Id。
        incoming = parse_traceparent(request.headers.get(TRACEPARENT_HEADER))
        if incoming:
            trace_id = incoming[0]
            parent_span = incoming[1]
            flags = incoming[2]
        else:
            trace_id = (
                request.headers.get(TRACE_HEADER)
                or request.headers.get(REQUEST_ID_HEADER)
                or f"tr_{uuid.uuid4().hex}"
            )
            parent_span = None
            flags = "01"

        root = Span(
            name=f"{request.method} {request.url.path}",
            service=service,
            trace_id=trace_id,
            span_id=_hex_id(16),
            parent_span_id=parent_span,
            kind="SERVER",
            attributes={"http.method": request.method, "http.route": request.url.path},
        )
        request.state.trace_id = trace_id
        request.state.span_id = root.span_id
        request.state.traceparent = format_traceparent(trace_id, root.span_id, flags)

        token_trace = trace_id_var.set(trace_id)
        token_span = span_id_var.set(root.span_id)
        token_flags = _trace_flags_var.set(flags)
        started = time.perf_counter()
        status = 500
        response = None
        error: BaseException | None = None

        from aipaas_common.core.context import request_id_ctx_var

        token_request = request_id_ctx_var.set(
            request.headers.get(REQUEST_ID_HEADER) or trace_id
        )
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except BaseException as exc:  # noqa: BLE001 - 记录后继续抛出
            error = exc
            raise
        finally:
            duration = time.perf_counter() - started
            root.attributes["http.status_code"] = status
            root.finish(duration * 1000, error)
            trace_store.record(root)
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            metrics.http_requests.inc(
                service=service, method=request.method, path=path, status=str(status)
            )
            metrics.http_duration.observe(duration, service=service, method=request.method, path=path)
            outcome = "SUCCESS" if status < 400 else ("DENIED" if status in (401, 403) else "FAILURE")
            _log_request(
                service=service,
                trace_id=trace_id,
                method=request.method,
                path=request.url.path,
                status=status,
                duration_ms=duration * 1000,
                user_id=request.headers.get("X-User-Id", ""),
                tenant_id=request.headers.get("X-Tenant-Id", ""),
                ip=request.headers.get("X-Forwarded-For") or (request.client.host if request.client else ""),
                outcome=outcome,
            )
            if response is not None:
                response.headers[TRACE_HEADER] = trace_id
                response.headers[TRACEPARENT_HEADER] = format_traceparent(trace_id, root.span_id, flags)
            request_id_ctx_var.reset(token_request)
            _trace_flags_var.reset(token_flags)
            span_id_var.reset(token_span)
            trace_id_var.reset(token_trace)

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> PlainTextResponse:
        return PlainTextResponse(metrics.render(service), media_type="text/plain; version=0.0.4")

    @app.get("/api/v1/observability/traces/{trace_id}", include_in_schema=False)
    async def get_trace(trace_id: str) -> dict[str, Any]:
        data = trace_snapshot(trace_id)
        return {"code": "OK", "message": "ok", "data": data, "traceId": trace_id}


def reset_for_tests() -> None:
    """测试隔离：清空 Trace 存储与计数器。"""
    trace_store.clear()
    global metrics
    metrics = MetricsRegistry()
