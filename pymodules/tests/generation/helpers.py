"""生成链路测试辅助：假供应商、假检索器与最小权限快照。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aipaas_generation import (
    LLMClientManager,
    LLMProviderConfig,
    LLMResponse,
    ProviderRegistry,
    RetrievedClause,
    TokenUsage,
)


@dataclass(slots=True)
class DemoAccess:
    user_id: str = "user-1"
    tenant_id: str = "tenant-1"
    permissions: frozenset[str] = frozenset({"chat:use", "retrieval:query"})

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions or "*" in self.permissions


class FakeRetriever:
    def __init__(self, clauses: list[RetrievedClause] | None = None, error: Exception | None = None):
        self.clauses = clauses or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def search(
        self,
        query: str,
        access,
        *,
        top_k: int,
        kb_id: str | None,
        kb_ids: list[str],
        filters,
        trace_id: str | None,
    ) -> list[RetrievedClause]:
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "kb_id": kb_id,
                "kb_ids": list(kb_ids),
                "trace_id": trace_id,
            }
        )
        if self.error is not None:
            raise self.error
        return list(self.clauses)


class FakeChatClient:
    """可编排的供应商客户端：记录调用次数，可返回内容或抛错。"""

    def __init__(
        self,
        *,
        provider: str,
        content: str = "依据 [1]，金融机构应当建立风险评估机制。",
        error: Exception | None = None,
        latency_ms: int = 12,
    ) -> None:
        self.provider = provider
        self.content = content
        self.error = error
        self.latency_ms = latency_ms
        self.calls: list[list[dict[str, str]]] = []

    def complete(
        self,
        messages,
        *,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.calls.append([dict(item) for item in messages])
        if self.error is not None:
            raise self.error
        return LLMResponse(
            provider=self.provider,
            model=model or f"{self.provider}-model",
            content=self.content,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            latency_ms=self.latency_ms,
        )


def build_manager(
    *,
    primary: FakeChatClient | None = None,
    secondary: FakeChatClient | None = None,
) -> LLMClientManager:
    primary = primary or FakeChatClient(provider="p1")
    secondary = secondary or FakeChatClient(provider="p2")
    registry = ProviderRegistry(
        [
            LLMProviderConfig(
                code="p1",
                name="供应商一",
                base_url="https://p1.example.com/v1",
                api_key="key-p1",
                default_model="p1-model",
                priority=10,
            ),
            LLMProviderConfig(
                code="p2",
                name="供应商二",
                base_url="https://p2.example.com/v1",
                api_key="key-p2",
                default_model="p2-model",
                priority=20,
            ),
        ]
    )
    return LLMClientManager(
        registry,
        clients={"p1": primary, "p2": secondary},
        max_attempts=2,
    )
