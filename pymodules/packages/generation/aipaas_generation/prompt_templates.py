"""#103 Prompt 模板库：检索条款 + 系统指令 + 用户问题。

- 模板带名称与版本，``templateId`` 形如 ``rag-answer@1``，写入答案与审计，版本可追溯；
- 上下文超长时按窗口策略截断/压缩，返回告警而不抛异常；
- ``PromptAssembly.describe()`` 只包含长度、哈希与条款 id，可直接落日志，不含 Prompt 正文。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Sequence

from aipaas_generation.errors import TemplateNotFound
from aipaas_generation.models import RetrievedClause

PURPOSE_ANSWER = "ANSWER"

DEFAULT_SYSTEM_INSTRUCTION = (
    "你是金融标准知识库问答助手，必须严格遵守：\n"
    "1. 只能依据【检索条款】作答，不得使用外部知识、常识补全或推测。\n"
    "2. 每个结论后必须用 [编号] 标注依据，编号必须来自【检索条款】。\n"
    "3. 检索条款不足以回答时，直接回答“检索到的条款不足以回答该问题”，并建议查阅原标准。\n"
    "4. 不得编造标准编号、条款编号、生效日期、数字或发布机构。\n"
    "5. 使用 Markdown 输出，可使用列表、引用与表格。"
)

DEFAULT_USER_TEMPLATE = (
    "【检索条款】\n"
    "{context}\n\n"
    "【用户问题】\n"
    "{question}\n\n"
    "请作答："
)


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """单个版本的 Prompt 模板；``updatedAt`` 便于追溯模板变更时间。"""

    name: str
    version: str
    system_instruction: str
    user_template: str
    purpose: str = PURPOSE_ANSWER
    variables: tuple[str, ...] = ("context", "question")
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self) -> None:
        if not self.name or not self.version:
            raise ValueError("模板名称与版本不能为空")

    @property
    def template_id(self) -> str:
        return f"{self.name}@{self.version}"

    def content_hash(self) -> str:
        payload = f"{self.system_instruction}\n---\n{self.user_template}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    def to_dict(self, *, active: bool) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "purpose": self.purpose,
            "variables": list(self.variables),
            "contentHash": self.content_hash(),
            "active": active,
            "updatedAt": self.updated_at,
        }


class PromptTemplateRegistry:
    """模板注册表：同名多版本共存，默认取最后注册的版本。"""

    def __init__(self) -> None:
        self._templates: dict[str, list[PromptTemplate]] = {}

    def register(self, template: PromptTemplate) -> None:
        versions = self._templates.setdefault(template.name, [])
        if any(item.version == template.version for item in versions):
            raise ValueError(f"模板版本已存在：{template.template_id}")
        versions.append(template)

    def get(self, name: str, version: str | None = None) -> PromptTemplate:
        versions = self._templates.get(name)
        if not versions:
            raise TemplateNotFound(f"Prompt 模板不存在：{name}")
        if version is None:
            return versions[-1]
        for item in versions:
            if item.version == version:
                return item
        raise TemplateNotFound(f"Prompt 模板版本不存在：{name}@{version}")

    def versions(self, name: str) -> list[PromptTemplate]:
        versions = self._templates.get(name)
        if not versions:
            raise TemplateNotFound(f"Prompt 模板不存在：{name}")
        return list(versions)

    def list_templates(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for versions in self._templates.values():
            latest = versions[-1]
            for item in versions:
                items.append(item.to_dict(active=item is latest))
        return items

    def list_versions(self, name: str) -> list[dict[str, Any]]:
        versions = self.versions(name)
        latest = versions[-1]
        return [item.to_dict(active=item is latest) for item in versions]

    @classmethod
    def with_builtin_defaults(cls) -> "PromptTemplateRegistry":
        registry = cls()
        registry.register(
            PromptTemplate(
                name="rag-answer",
                version="1",
                system_instruction=DEFAULT_SYSTEM_INSTRUCTION,
                user_template=DEFAULT_USER_TEMPLATE,
            )
        )
        return registry


@dataclass(slots=True)
class ContextWindowPolicy:
    """上下文窗口策略：字符预算是硬约束，超限时先压缩单条，再丢弃低排名条款。"""

    max_context_chars: int = 6000
    min_clause_chars: int = 120
    max_clauses: int = 8
    per_clause_chars: int | None = None

    def __post_init__(self) -> None:
        if self.max_context_chars < 1:
            raise ValueError("max_context_chars 必须为正整数")
        if self.max_clauses < 1:
            raise ValueError("max_clauses 必须为正整数")
        if self.per_clause_chars is not None and self.per_clause_chars < 1:
            raise ValueError("per_clause_chars 必须为正整数")


@dataclass(slots=True)
class PromptAssembly:
    """组装结果；``describe()`` 可安全写入日志与审计。"""

    system_prompt: str
    user_prompt: str
    template_id: str
    content_hash: str
    used_clause_ids: list[str] = field(default_factory=list)
    used_clauses: list[RetrievedClause] = field(default_factory=list)
    dropped_clause_ids: list[str] = field(default_factory=list)
    truncated: bool = False
    context_chars: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def prompt(self) -> str:
        return f"{self.system_prompt}\n\n{self.user_prompt}"

    @property
    def prompt_chars(self) -> int:
        return len(self.prompt)

    def fingerprint(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()[:16]

    def describe(self) -> dict[str, Any]:
        """仅元信息：禁止把 Prompt 正文写入审计或日志。"""
        return {
            "templateId": self.template_id,
            "contentHash": self.content_hash,
            "promptFingerprint": self.fingerprint(),
            "promptChars": self.prompt_chars,
            "contextChars": self.context_chars,
            "usedClauses": len(self.used_clause_ids),
            "droppedClauses": len(self.dropped_clause_ids),
            "truncated": self.truncated,
        }


def _format_clause(index: int, clause: RetrievedClause) -> str:
    body = " ".join(clause.text.split())
    return f"[{index}] {clause.header()}\n{body}"


def select_clauses(
    clauses: Sequence[RetrievedClause], policy: ContextWindowPolicy
) -> tuple[list[tuple[int, RetrievedClause, str]], list[str], bool]:
    """按排名选取条款并施加字符预算；返回（保留条款, 丢弃 chunkId, 是否截断）。"""
    kept: list[tuple[int, RetrievedClause, str]] = []
    dropped: list[str] = []
    truncated = False
    used_chars = 0
    for clause in clauses:
        if len(kept) >= policy.max_clauses:
            dropped.append(clause.chunk_id)
            continue
        index = len(kept) + 1
        body = " ".join(clause.text.split())
        if policy.per_clause_chars and len(body) > policy.per_clause_chars:
            body = body[: policy.per_clause_chars].rstrip() + "…"
            truncated = True
        header = f"[{index}] {clause.header()}\n"
        remaining = policy.max_context_chars - used_chars
        if remaining <= len(header) + 1:
            dropped.append(clause.chunk_id)
            continue
        max_body = remaining - len(header) - 2
        if len(body) > max_body:
            # 压缩到窗口剩余空间，保留可读片段；不因超长报错。
            body = body[: max(1, max_body)].rstrip() + "…"
            truncated = True
        entry = header + body
        kept.append((index, clause, entry))
        used_chars += len(entry) + 2
    if not kept and clauses:
        # 极端小窗口：至少保留第一条的最小片段，保证调用方拿到可用 Prompt。
        first = clauses[0]
        body = " ".join(first.text.split())[: max(1, policy.min_clause_chars)]
        kept.append((1, first, _format_clause(1, replace(first, text=body + "…"))))
        dropped.extend(item.chunk_id for item in clauses[1:])
        truncated = True
    return kept, dropped, truncated


def assemble_prompt(
    template: PromptTemplate,
    clauses: Sequence[RetrievedClause],
    question: str,
    *,
    policy: ContextWindowPolicy | None = None,
) -> PromptAssembly:
    """组装 Prompt：系统指令 + 检索条款 + 用户问题；超长按窗口策略处理。"""
    window = policy or ContextWindowPolicy()
    kept, dropped, truncated = select_clauses(clauses, window)
    context = "\n\n".join(entry for _, _, entry in kept) or "无检索条款"
    user_prompt = template.user_template.format(context=context, question=question.strip())
    warnings: list[str] = []
    if truncated:
        warnings.append(
            f"CONTEXT_TRUNCATED: 上下文超过 {window.max_context_chars} 字符，已按窗口策略截断/压缩"
        )
    if dropped:
        warnings.append(f"CONTEXT_CLAUSES_DROPPED: {len(dropped)} 条低相关条款未纳入上下文")
    return PromptAssembly(
        system_prompt=template.system_instruction,
        user_prompt=user_prompt,
        template_id=template.template_id,
        content_hash=template.content_hash(),
        used_clause_ids=[clause.chunk_id for _, clause, _ in kept],
        used_clauses=[clause for _, clause, _ in kept],
        dropped_clause_ids=dropped,
        truncated=truncated,
        context_chars=len(context),
        warnings=warnings,
    )
