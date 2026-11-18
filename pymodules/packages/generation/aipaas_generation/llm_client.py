"""#98 LLM 基础调用客户端（非网关）。

P0 能力边界：
- 至少两家 OpenAI 兼容供应商直连，可手动切换（``switch`` / 请求级 ``provider``）；
- 主供应商失败时按优先级做一次简单故障切换，返回明确的降级提示；
- 全部失败抛出 ``ModelUnavailable``，携带脱敏的尝试链；
- 密钥只存在于内存配置，永不写入日志与异常信息。

不做（归 P1 #99/#100/#240）：按成本/质量/租户的策略路由、熔断半开、版本回滚。
对外 OpenAI 兼容接口 ``/v1/chat/completions`` 归 P1 #411，本模块只服务内部
``/internal/llm/chat/completions``。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Protocol, Sequence

import httpx

from aipaas_generation.errors import GenerationError, ModelUnavailable
from aipaas_generation.models import LLMResponse, TokenUsage

logger = logging.getLogger(__name__)

DEFAULT_TEMPERATURE = 0.1
DEFAULT_MAX_TOKENS = 1024
MAX_ERROR_TEXT = 160


class LLMClientError(GenerationError):
    """单次供应商调用失败：``retryable`` 决定是否值得切换到下一家。"""

    def __init__(self, message: str, *, code: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class LLMProviderConfig:
    """供应商配置；``api_key`` 不参与 repr，避免日志/断言意外打印密钥。"""

    code: str
    name: str
    base_url: str
    api_key: str = field(default="", repr=False)
    models: tuple[str, ...] = ()
    default_model: str = ""
    timeout_s: float = 30.0
    priority: int = 100
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code or not self.base_url:
            raise ValueError("供应商 code 与 base_url 必须非空")
        if not self.default_model and self.models:
            object.__setattr__(self, "default_model", self.models[0])
        if self.timeout_s <= 0:
            raise ValueError("timeout_s 必须为正数")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def masked_key(self) -> str:
        return "***" if self.api_key else ""


def sanitize_text(text: Any, secrets: Sequence[str] = ()) -> str:
    """移除密钥、压缩空白并截断，确保错误信息可以安全落日志。"""
    value = str(text or "")
    for secret in secrets:
        if secret and len(secret) >= 4:
            value = value.replace(secret, "***")
    return " ".join(value.split())[:MAX_ERROR_TEXT]


class ChatCompletionClient(Protocol):
    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResponse: ...


@dataclass(slots=True)
class StreamChunk:
    """供应商流式分片：正文增量或末尾用量（OpenAI ``stream_options.include_usage``）。"""

    delta: str = ""
    usage: TokenUsage | None = None


@dataclass(slots=True)
class StreamEvent:
    """管理器的流式事件：``meta`` → 0..n ``delta`` → ``end``。"""

    kind: str
    provider: str = ""
    model: str = ""
    delta: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    degraded: bool = False
    warnings: list[str] = field(default_factory=list)
    first_token_ms: int | None = None


class OpenAICompatibleClient:
    """OpenAI 兼容 ``/chat/completions`` 直连客户端。"""

    def __init__(
        self,
        config: LLMProviderConfig,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._client = client or httpx.Client(
            timeout=config.timeout_s, transport=transport, follow_redirects=True
        )

    @property
    def endpoint(self) -> str:
        return self.config.base_url.rstrip("/") + "/chat/completions"

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResponse:
        if not self.config.configured:
            raise LLMClientError(
                f"供应商 {self.config.code} 未配置 API Key", code="MODEL_NOT_CONFIGURED", retryable=False
            )
        target_model = model or self.config.default_model
        if not target_model:
            raise LLMClientError(
                f"供应商 {self.config.code} 未配置模型", code="MODEL_NOT_CONFIGURED", retryable=False
            )
        payload = {
            "model": target_model,
            "messages": [dict(item) for item in messages],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "stream": False,
        }
        headers = {"Content-Type": "application/json", **dict(self.config.headers)}
        headers.setdefault("Authorization", f"Bearer {self.config.api_key}")

        # 只记录元信息：供应商、模型、消息条数与字符数，绝不输出 Prompt 正文。
        prompt_chars = sum(len(str(item.get("content", ""))) for item in messages)
        logger.debug(
            "LLM 请求 provider=%s model=%s messages=%d chars=%d",
            self.config.code,
            target_model,
            len(messages),
            prompt_chars,
        )

        started = time.perf_counter()
        try:
            response = self._client.post(self.endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMClientError(
                f"供应商 {self.config.code} 超时", code="MODEL_TIMEOUT", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMClientError(
                sanitize_text(str(exc), [self.config.api_key]), code="MODEL_NETWORK_ERROR"
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        status_error = self._status_error(response)
        if status_error is not None:
            raise status_error

        try:
            body = response.json()
            choice = (body.get("choices") or [])[0]
            content = (choice.get("message") or {}).get("content")
        except (ValueError, IndexError, AttributeError, TypeError) as exc:
            raise LLMClientError(
                f"供应商 {self.config.code} 返回结构无法解析", code="MODEL_BAD_RESPONSE"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMClientError(
                f"供应商 {self.config.code} 返回空内容", code="MODEL_EMPTY_RESPONSE"
            )

        usage_raw = body.get("usage") or {}
        return LLMResponse(
            provider=self.config.code,
            model=str(body.get("model") or target_model),
            content=content,
            usage=TokenUsage(
                prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                completion_tokens=int(usage_raw.get("completion_tokens") or 0),
                total_tokens=int(usage_raw.get("total_tokens") or 0),
            ),
            latency_ms=latency_ms,
            attempts=[],
        )

    @staticmethod
    def _safe_body(response: httpx.Response) -> str:
        try:
            return response.text
        except Exception:  # pragma: no cover - 极端编码异常
            return ""

    def _status_error(self, response: httpx.Response) -> LLMClientError | None:
        """统一 HTTP 状态映射；返回异常而非抛出，供非流式与流式共用。"""
        status = response.status_code
        if status in (401, 403):
            return LLMClientError(
                f"供应商 {self.config.code} 鉴权失败（HTTP {status}）",
                code="MODEL_AUTH_FAILED",
                retryable=False,
            )
        if status == 429:
            return LLMClientError(
                f"供应商 {self.config.code} 触发限流（HTTP 429）",
                code="MODEL_RATE_LIMITED",
                retryable=True,
            )
        if status >= 500:
            return LLMClientError(
                f"供应商 {self.config.code} 服务异常（HTTP {status}）",
                code="MODEL_HTTP_ERROR",
                retryable=True,
            )
        if status >= 400:
            detail = sanitize_text(self._safe_body(response), [self.config.api_key])
            return LLMClientError(
                f"供应商 {self.config.code} 请求失败（HTTP {status}）：{detail}",
                code="MODEL_BAD_REQUEST",
                retryable=False,
            )
        return None

    def stream(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Iterator[StreamChunk]:
        """OpenAI 兼容流式调用：按 SSE ``data:`` 行产出增量与末尾用量。

        错误映射与非流式一致；流中异常统一为 ``MODEL_STREAM_ERROR``，
        由管理器决定是否在“尚未输出任何 token”时切换供应商。
        """
        if not self.config.configured:
            raise LLMClientError(
                f"供应商 {self.config.code} 未配置 API Key", code="MODEL_NOT_CONFIGURED", retryable=False
            )
        target_model = model or self.config.default_model
        if not target_model:
            raise LLMClientError(
                f"供应商 {self.config.code} 未配置模型", code="MODEL_NOT_CONFIGURED", retryable=False
            )
        payload = {
            "model": target_model,
            "messages": [dict(item) for item in messages],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        headers = {"Content-Type": "application/json", **dict(self.config.headers)}
        headers.setdefault("Authorization", f"Bearer {self.config.api_key}")
        logger.debug(
            "LLM 流式请求 provider=%s model=%s messages=%d chars=%d",
            self.config.code,
            target_model,
            len(messages),
            sum(len(str(item.get("content", ""))) for item in messages),
        )
        try:
            with self._client.stream("POST", self.endpoint, json=payload, headers=headers) as response:
                if response.status_code >= 400:
                    response.read()
                    status_error = self._status_error(response)
                    if status_error is not None:
                        raise status_error
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    if raw == "[DONE]":
                        break
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    choices = event.get("choices") or []
                    if choices:
                        delta = (choices[0].get("delta") or {}).get("content")
                        if isinstance(delta, str) and delta:
                            yield StreamChunk(delta=delta)
                    usage = event.get("usage")
                    if isinstance(usage, dict):
                        yield StreamChunk(
                            usage=TokenUsage(
                                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                                completion_tokens=int(usage.get("completion_tokens") or 0),
                                total_tokens=int(usage.get("total_tokens") or 0),
                            )
                        )
        except httpx.TimeoutException as exc:
            raise LLMClientError(
                f"供应商 {self.config.code} 流式超时", code="MODEL_TIMEOUT", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMClientError(
                sanitize_text(str(exc), [self.config.api_key]), code="MODEL_STREAM_ERROR"
            ) from exc


class ProviderStats:
    """单供应商可用率与延迟统计（P95 用于 /models/health 展示）。"""

    def __init__(self, window: int = 200) -> None:
        self.total = 0
        self.success = 0
        self.failure = 0
        self._latencies: deque[int] = deque(maxlen=window)
        self._lock = threading.RLock()

    def record(self, ok: bool, latency_ms: int) -> None:
        with self._lock:
            self.total += 1
            if ok:
                self.success += 1
            else:
                self.failure += 1
            self._latencies.append(max(0, int(latency_ms)))

    @property
    def success_rate(self) -> float:
        with self._lock:
            return 1.0 if self.total == 0 else self.success / self.total

    def p95_latency_ms(self) -> int | None:
        with self._lock:
            if not self._latencies:
                return None
            values = sorted(self._latencies)
            index = min(len(values) - 1, max(0, int(round(0.95 * (len(values) - 1)))))
            return values[index]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total": self.total,
                "success": self.success,
                "failure": self.failure,
                "successRate": round(self.success_rate, 6),
                "latencyP95Ms": self.p95_latency_ms(),
            }


class AvailabilityTracker:
    """跨供应商可用率汇总；成功率达标的判定口径为 ``success/total``。"""

    def __init__(self) -> None:
        self._stats: dict[str, ProviderStats] = {}
        self._lock = threading.RLock()

    def stats(self, provider: str) -> ProviderStats:
        with self._lock:
            return self._stats.setdefault(provider, ProviderStats())

    def record(self, provider: str, ok: bool, latency_ms: int) -> None:
        self.stats(provider).record(ok, latency_ms)

    def success_rate(self, provider: str | None = None) -> float:
        with self._lock:
            if provider is not None:
                return self.stats(provider).success_rate
            total = sum(item.total for item in self._stats.values())
            success = sum(item.success for item in self._stats.values())
            return 1.0 if total == 0 else success / total

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            providers = {code: stats.snapshot() for code, stats in self._stats.items()}
            total = sum(item.total for item in self._stats.values())
            success = sum(item.success for item in self._stats.values())
            return {
                "providers": providers,
                "overall": {
                    "total": total,
                    "success": success,
                    "successRate": round(1.0 if total == 0 else success / total, 6),
                },
            }


class ProviderRegistry:
    """供应商注册表：维护激活供应商与健康状态，支持手动切换。"""

    def __init__(
        self,
        configs: Sequence[LLMProviderConfig],
        *,
        active: str | None = None,
        tracker: AvailabilityTracker | None = None,
    ) -> None:
        if not configs:
            raise ValueError("至少需要一家 LLM 供应商")
        self._configs = {config.code: config for config in configs}
        if len(self._configs) != len(configs):
            raise ValueError("供应商 code 不能重复")
        self.tracker = tracker or AvailabilityTracker()
        initial = active or configs[0].code
        if initial not in self._configs:
            raise ValueError(f"激活供应商不存在：{initial}")
        self._active = initial
        self._down: dict[str, str] = {}
        self._lock = threading.RLock()

    @property
    def active_code(self) -> str:
        with self._lock:
            return self._active

    def codes(self) -> list[str]:
        return list(self._configs)

    def get(self, code: str) -> LLMProviderConfig:
        try:
            return self._configs[code]
        except KeyError as exc:
            raise ModelUnavailable(f"未知的 LLM 供应商：{code}", retryable=False) from exc

    def switch(self, code: str) -> LLMProviderConfig:
        """手动切换激活供应商；未知 code 明确报错。"""
        config = self.get(code)
        with self._lock:
            self._active = code
        logger.info("LLM 供应商已手动切换 active=%s", code)
        return config

    def resolve_order(self, preferred: str | None = None) -> list[LLMProviderConfig]:
        """故障切换顺序：指定供应商 → 当前激活 → 其余按优先级。"""
        if preferred:
            self.get(preferred)
        with self._lock:
            head = preferred or self._active
            rest = sorted(
                (config for code, config in self._configs.items() if code != head),
                key=lambda item: (item.priority, item.code),
            )
            return [self._configs[head], *rest]

    def mark_unavailable(self, code: str, reason: str) -> None:
        with self._lock:
            self._down[code] = reason

    def mark_available(self, code: str) -> None:
        with self._lock:
            self._down.pop(code, None)

    def status(self, code: str) -> str:
        config = self._configs[code]
        if code in self._down or not config.configured:
            return "DOWN"
        stats = self.tracker.stats(code)
        if stats.failure and stats.success_rate < 0.999:
            return "DEGRADED"
        return "UP"

    def items(self) -> list[dict[str, Any]]:
        """对齐 OpenAPI ``ModelProvider``，并补充 ``active`` 便于前端展示切换状态。"""
        items = []
        for code in self.codes():
            config = self._configs[code]
            items.append(
                {
                    "code": code,
                    "name": config.name,
                    "models": list(config.models) or ([config.default_model] if config.default_model else []),
                    "status": self.status(code),
                    "latencyP95Ms": self.tracker.stats(code).p95_latency_ms(),
                    "active": code == self._active,
                }
            )
        return items


class LLMClientManager:
    """双供应商直连 + 简单故障切换；日志与异常均不包含 Prompt 正文与密钥。"""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        clients: Mapping[str, ChatCompletionClient] | None = None,
        tracker: AvailabilityTracker | None = None,
        max_attempts: int = 2,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须为正整数")
        self.registry = registry
        self.tracker = tracker or registry.tracker
        self.max_attempts = max_attempts
        self._clients: dict[str, ChatCompletionClient] = dict(clients or {})

    def _client_for(self, config: LLMProviderConfig) -> ChatCompletionClient:
        client = self._clients.get(config.code)
        if client is None:
            client = OpenAICompatibleClient(config)
            self._clients[config.code] = client
        return client

    def switch(self, code: str) -> None:
        self.registry.switch(code)

    @property
    def active_provider(self) -> str:
        return self.registry.active_code

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResponse:
        order = self.registry.resolve_order(provider)
        attempts: list[dict[str, Any]] = []
        warnings: list[str] = []
        last_retryable = True

        for index, config in enumerate(order[: self.max_attempts]):
            attempt_model = model or config.default_model
            started = time.perf_counter()
            try:
                response = self._client_for(config).complete(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except LLMClientError as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                self.tracker.record(config.code, False, latency_ms)
                attempts.append(
                    {
                        "provider": config.code,
                        "model": attempt_model,
                        "ok": False,
                        "errorCode": exc.code,
                        "error": sanitize_text(str(exc), [config.api_key]),
                        "latencyMs": latency_ms,
                    }
                )
                last_retryable = exc.retryable
                if not exc.retryable:
                    self.registry.mark_unavailable(config.code, exc.code)
                logger.warning(
                    "LLM 调用失败 provider=%s model=%s code=%s latencyMs=%d",
                    config.code,
                    attempt_model,
                    exc.code,
                    latency_ms,
                )
                remaining = index + 1 < min(len(order), self.max_attempts)
                if remaining:
                    warnings.append(
                        f"LLM_FAILOVER: 供应商 {config.code} 不可用（{exc.code}），"
                        f"已切换至 {order[index + 1].code}"
                    )
                continue
            except Exception as exc:  # pragma: no cover - 客户端实现异常
                latency_ms = int((time.perf_counter() - started) * 1000)
                self.tracker.record(config.code, False, latency_ms)
                attempts.append(
                    {
                        "provider": config.code,
                        "model": attempt_model,
                        "ok": False,
                        "errorCode": "MODEL_CLIENT_ERROR",
                        "error": sanitize_text(str(exc), [config.api_key]),
                        "latencyMs": latency_ms,
                    }
                )
                logger.warning(
                    "LLM 客户端异常 provider=%s code=MODEL_CLIENT_ERROR", config.code
                )
                continue

            self.tracker.record(config.code, True, response.latency_ms)
            self.registry.mark_available(config.code)
            attempts.append(
                {
                    "provider": config.code,
                    "model": response.model,
                    "ok": True,
                    "latencyMs": response.latency_ms,
                }
            )
            response.attempts = attempts
            response.degraded = index > 0
            if index > 0:
                warnings.insert(
                    0,
                    f"LLM_FAILOVER: 主供应商不可用，已切换至 {config.code}（{config.name}）",
                )
            response.warnings = list(warnings) + list(response.warnings)
            logger.info(
                "LLM 调用成功 provider=%s model=%s latencyMs=%d degraded=%s",
                config.code,
                response.model,
                response.latency_ms,
                response.degraded,
            )
            return response

        logger.error("全部 LLM 供应商不可用 attempts=%d", len(attempts))
        raise ModelUnavailable(
            "所有 LLM 供应商均不可用，请稍后重试或查阅原标准",
            attempts=attempts,
            retryable=last_retryable,
        )

    def stream(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Iterator[StreamEvent]:
        """流式调用：首个 token 前失败可切换供应商，输出了 token 后失败不再重放。

        事件序列固定为 ``meta`` → 0..n ``delta`` → ``end``；全部供应商不可用
        抛出 ``ModelUnavailable``。未实现 ``stream`` 的客户端回退为非流式单分片。
        """
        order = self.registry.resolve_order(provider)
        attempts: list[dict[str, Any]] = []
        warnings: list[str] = []
        last_retryable = True

        for index, config in enumerate(order[: self.max_attempts]):
            attempt_model = model or config.default_model
            started = time.perf_counter()
            client = self._client_for(config)
            try:
                iterator = self._open_stream(client, messages, model, temperature, max_tokens)
                first_chunk = next(iterator, None)
            except LLMClientError as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                self.tracker.record(config.code, False, latency_ms)
                last_retryable = exc.retryable
                attempts.append(
                    {
                        "provider": config.code,
                        "model": attempt_model,
                        "ok": False,
                        "errorCode": exc.code,
                        "error": sanitize_text(str(exc), [config.api_key]),
                        "latencyMs": latency_ms,
                    }
                )
                if not exc.retryable:
                    self.registry.mark_unavailable(config.code, exc.code)
                logger.warning(
                    "LLM 流式调用失败 provider=%s model=%s code=%s",
                    config.code,
                    attempt_model,
                    exc.code,
                )
                if index + 1 < min(len(order), self.max_attempts):
                    warnings.append(
                        f"LLM_FAILOVER: 供应商 {config.code} 不可用（{exc.code}），"
                        f"已切换至 {order[index + 1].code}"
                    )
                continue
            except Exception:  # pragma: no cover - 客户端实现异常
                latency_ms = int((time.perf_counter() - started) * 1000)
                self.tracker.record(config.code, False, latency_ms)
                attempts.append(
                    {
                        "provider": config.code,
                        "model": attempt_model,
                        "ok": False,
                        "errorCode": "MODEL_CLIENT_ERROR",
                        "latencyMs": latency_ms,
                    }
                )
                continue

            degraded = index > 0
            if degraded:
                warnings.insert(
                    0,
                    f"LLM_FAILOVER: 主供应商不可用，已切换至 {config.code}（{config.name}）",
                )
            yield StreamEvent(
                "meta",
                provider=config.code,
                model=attempt_model,
                degraded=degraded,
                warnings=list(warnings),
            )

            usage = TokenUsage()
            first_token_ms: int | None = None
            try:
                for chunk in self._chain(first_chunk, iterator):
                    if chunk.delta:
                        if first_token_ms is None:
                            first_token_ms = int((time.perf_counter() - started) * 1000)
                        yield StreamEvent("delta", delta=chunk.delta)
                    if chunk.usage is not None:
                        usage = chunk.usage
            except LLMClientError as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                self.tracker.record(config.code, False, latency_ms)
                self.registry.mark_unavailable(config.code, exc.code)
                attempts.append(
                    {
                        "provider": config.code,
                        "model": attempt_model,
                        "ok": False,
                        "errorCode": exc.code,
                        "latencyMs": latency_ms,
                    }
                )
                logger.warning(
                    "LLM 流式输出中断 provider=%s code=%s", config.code, exc.code
                )
                raise ModelUnavailable(
                    "LLM 流式输出中断，请重试或查阅原标准",
                    attempts=attempts,
                    retryable=True,
                ) from exc

            latency_ms = int((time.perf_counter() - started) * 1000)
            self.tracker.record(config.code, True, latency_ms)
            self.registry.mark_available(config.code)
            yield StreamEvent(
                "end",
                provider=config.code,
                model=attempt_model,
                usage=usage,
                degraded=degraded,
                warnings=list(warnings),
                first_token_ms=first_token_ms,
            )
            return

        logger.error("全部 LLM 供应商流式调用不可用 attempts=%d", len(attempts))
        raise ModelUnavailable(
            "所有 LLM 供应商均不可用，请稍后重试或查阅原标准",
            attempts=attempts,
            retryable=last_retryable,
        )

    def _open_stream(
        self,
        client: ChatCompletionClient,
        messages: Sequence[Mapping[str, str]],
        model: str | None,
        temperature: float,
        max_tokens: int,
    ) -> Iterator[StreamChunk]:
        """优先使用客户端流式能力；不具备时回退为非流式单分片。"""
        stream_fn = getattr(client, "stream", None)
        if callable(stream_fn):
            return stream_fn(
                messages, model=model, temperature=temperature, max_tokens=max_tokens
            )

        def fallback() -> Iterator[StreamChunk]:
            response = client.complete(
                messages, model=model, temperature=temperature, max_tokens=max_tokens
            )
            yield StreamChunk(delta=response.content, usage=response.usage)

        return fallback()

    @staticmethod
    def _chain(
        first: StreamChunk | None, rest: Iterator[StreamChunk]
    ) -> Iterator[StreamChunk]:
        if first is not None:
            yield first
        yield from rest

    def health(self) -> dict[str, Any]:
        return {"items": self.registry.items(), "availability": self.tracker.snapshot()}


def build_default_llm_manager() -> LLMClientManager:
    """按环境变量装配两家供应商；未配置密钥的供应商保留在列表中并标记 DOWN。"""
    primary_code = os.getenv("AIPAAS_LLM_PRIMARY_PROVIDER", "qwen")
    secondary_code = os.getenv("AIPAAS_LLM_SECONDARY_PROVIDER", "openai")
    primary = LLMProviderConfig(
        code=primary_code,
        name=os.getenv("AIPAAS_LLM_PRIMARY_NAME", "通义千问（DashScope 兼容）"),
        base_url=os.getenv(
            "AIPAAS_LLM_PRIMARY_BASE_URL",
            os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ),
        api_key=os.getenv("AIPAAS_LLM_PRIMARY_API_KEY", os.getenv("OPENAI_API_KEY", "")),
        default_model=os.getenv(
            "AIPAAS_LLM_PRIMARY_MODEL", os.getenv("LLM_MODEL", "qwen-plus")
        ),
        priority=10,
    )
    secondary = LLMProviderConfig(
        code=secondary_code,
        name=os.getenv("AIPAAS_LLM_SECONDARY_NAME", "OpenAI 兼容"),
        base_url=os.getenv("AIPAAS_LLM_SECONDARY_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.getenv("AIPAAS_LLM_SECONDARY_API_KEY", ""),
        default_model=os.getenv("AIPAAS_LLM_SECONDARY_MODEL", "gpt-4o-mini"),
        priority=20,
    )
    if primary.code == secondary.code:
        secondary = LLMProviderConfig(
            code=f"{secondary.code}-2",
            name=secondary.name,
            base_url=secondary.base_url,
            api_key=secondary.api_key,
            default_model=secondary.default_model,
            priority=20,
        )
    registry = ProviderRegistry([primary, secondary], active=os.getenv("AIPAAS_LLM_PROVIDER") or primary.code)
    return LLMClientManager(registry)
