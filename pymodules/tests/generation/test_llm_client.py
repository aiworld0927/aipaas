"""#98 LLM 基础调用测试：双供应商切换、故障切换、可用率与密钥安全。"""

from __future__ import annotations

import logging

import httpx
import pytest

from aipaas_generation import (
    AvailabilityTracker,
    LLMClientError,
    LLMProviderConfig,
    ModelUnavailable,
    OpenAICompatibleClient,
    ProviderRegistry,
)
from helpers import FakeChatClient, build_manager


def _config(**overrides) -> LLMProviderConfig:
    values = {
        "code": "p1",
        "name": "供应商一",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-secret-key-123456",
        "default_model": "demo-model",
    }
    values.update(overrides)
    return LLMProviderConfig(**values)


def test_manual_switch_and_request_override():
    primary = FakeChatClient(provider="p1")
    secondary = FakeChatClient(provider="p2")
    manager = build_manager(primary=primary, secondary=secondary)

    manager.switch("p2")
    assert manager.active_provider == "p2"
    response = manager.complete([{"role": "user", "content": "问题"}])
    assert response.provider == "p2"
    assert response.degraded is False
    assert primary.calls == []

    manager.complete([{"role": "user", "content": "问题"}], provider="p1")
    assert len(primary.calls) == 1


def test_failover_to_secondary(timeout_error):
    primary = FakeChatClient(provider="p1", error=timeout_error)
    secondary = FakeChatClient(provider="p2", content="已切换供应商的回答 [1]")
    manager = build_manager(primary=primary, secondary=secondary)

    response = manager.complete([{"role": "user", "content": "问题"}])
    assert response.provider == "p2"
    assert response.degraded is True
    assert [item["provider"] for item in response.attempts] == ["p1", "p2"]
    assert response.attempts[0]["ok"] is False
    assert response.attempts[0]["errorCode"] == "MODEL_TIMEOUT"
    assert any("LLM_FAILOVER" in warning for warning in response.warnings)
    assert len(primary.calls) == 1 and len(secondary.calls) == 1


def test_all_providers_unavailable(timeout_error):
    manager = build_manager(
        primary=FakeChatClient(provider="p1", error=timeout_error),
        secondary=FakeChatClient(
            provider="p2", error=LLMClientError("服务异常", code="MODEL_HTTP_ERROR")
        ),
    )
    with pytest.raises(ModelUnavailable) as info:
        manager.complete([{"role": "user", "content": "问题"}])
    assert len(info.value.attempts) == 2
    assert info.value.retryable is True
    assert "不可用" in str(info.value)


def test_unknown_provider_rejected():
    manager = build_manager()
    with pytest.raises(ModelUnavailable):
        manager.switch("not-exists")
    with pytest.raises(ModelUnavailable):
        manager.complete([{"role": "user", "content": "问题"}], provider="not-exists")


def test_openai_client_parses_response_and_usage():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "model": "demo-model-2026",
                "choices": [{"message": {"role": "assistant", "content": "回答 [1]"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            },
        )

    llm = OpenAICompatibleClient(
        _config(), client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    response = llm.complete([{"role": "user", "content": "问题"}])
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-secret-key-123456"
    assert response.model == "demo-model-2026"
    assert response.usage.total_tokens == 18
    assert response.content == "回答 [1]"


def test_secret_never_in_error_or_repr():
    config = _config()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": f"invalid api key {config.api_key} for request"}
        )

    llm = OpenAICompatibleClient(
        config, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(LLMClientError) as info:
        llm.complete([{"role": "user", "content": "问题"}])
    assert config.api_key not in str(info.value)
    assert config.api_key not in repr(config)
    assert config.masked_key() == "***"
    assert "***" in str(info.value)


def test_prompt_and_secret_not_logged(caplog):
    caplog.set_level(logging.DEBUG)
    config = _config()
    prompt_text = "绝密用户问题-ABC123-不得落日志"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "demo-model",
                "choices": [{"message": {"content": "回答"}}],
                "usage": {},
            },
        )

    llm = OpenAICompatibleClient(
        config, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    llm.complete([{"role": "user", "content": prompt_text}])
    assert prompt_text not in caplog.text
    assert config.api_key not in caplog.text


def test_success_rate_meets_999_target():
    tracker = AvailabilityTracker()
    for _ in range(999):
        tracker.record("p1", True, 10)
    tracker.record("p1", False, 30)
    assert tracker.success_rate("p1") == pytest.approx(0.999)
    assert tracker.success_rate("p1") >= 0.999

    snapshot = tracker.snapshot()
    assert snapshot["providers"]["p1"]["total"] == 1000
    assert snapshot["providers"]["p1"]["successRate"] >= 0.999


def test_provider_status_and_switching_state():
    registry = ProviderRegistry(
        [
            _config(),
            _config(code="p2", name="供应商二", api_key="", default_model="m2", priority=20),
        ]
    )
    items = registry.items()
    assert [item["code"] for item in items] == ["p1", "p2"]
    assert items[0]["active"] is True
    assert items[1]["status"] == "DOWN"  # 未配置密钥
    registry.switch("p2")
    assert registry.active_code == "p2"
    assert registry.items()[1]["active"] is True
