"""#88 行级权限模型：角色/知识库权限 + 密级，供召回阶段前置过滤。

约定：
- 调用方每个请求实时解析 ``AccessContext``（不缓存角色与 KB 授权），保证权限变更立即生效；
- 检索服务只信任 ``AccessContext``：显式请求越权 KB 直接拒绝并审计；
- 召回后端拿到 ``allowed_kb_ids`` 与 ``max_security_level`` 做前置过滤，服务再兜底复核。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

PERMISSION_RETRIEVAL_QUERY = "retrieval:query"
PERMISSION_KB_MANAGE = "kb:manage"

#: 密级从低到高；未知密级按最高级处理（拒绝），避免脏数据泄露。
SECURITY_LEVELS = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET")
UNKNOWN_LEVEL_RANK = len(SECURITY_LEVELS)


def level_rank(level: str | None) -> int:
    normalized = str(level or "PUBLIC").strip().upper()
    try:
        return SECURITY_LEVELS.index(normalized)
    except ValueError:
        return UNKNOWN_LEVEL_RANK


def can_read_level(record_level: str | None, max_level: str | None) -> bool:
    return level_rank(record_level) <= level_rank(max_level)


@dataclass(frozen=True, slots=True)
class AccessContext:
    """请求主体权限快照：全局权限点 + 知识库级授权 + 密级上限。"""

    user_id: str
    tenant_id: str = "default"
    roles: tuple[str, ...] = ()
    permissions: frozenset[str] = frozenset()
    kb_permissions: Mapping[str, frozenset[str]] = field(default_factory=dict)
    all_kbs: bool = False
    max_security_level: str = "PUBLIC"

    def has_permission(self, code: str) -> bool:
        return "*" in self.permissions or code in self.permissions

    def can_query_kb(self, kb_id: str | None) -> bool:
        """全局拥有检索权限，且该知识库授予检索或管理权限（管理员放行全部）。"""
        if not kb_id:
            return False
        if not self.has_permission(PERMISSION_RETRIEVAL_QUERY):
            return False
        if "*" in self.permissions or self.all_kbs:
            return True
        granted = self.kb_permissions.get(kb_id, frozenset())
        return PERMISSION_RETRIEVAL_QUERY in granted or PERMISSION_KB_MANAGE in granted

    def authorized_kb_ids(self) -> tuple[str, ...] | None:
        """可检索的知识库集合；``None`` 表示不受知识库维度限制。"""
        if "*" in self.permissions or self.all_kbs:
            return None
        return tuple(sorted(kb for kb in self.kb_permissions if self.can_query_kb(kb)))

    def can_read_level(self, record_level: str | None) -> bool:
        return can_read_level(record_level, self.max_security_level)


class AccessResolver(Protocol):
    """按请求解析权限快照；实现方须实时查询，不得缓存。"""

    def resolve(self) -> AccessContext: ...


class StaticAccessResolver:
    """静态解析器：用于本地开发与测试，不缓存任何外部状态。"""

    def __init__(self, context: AccessContext) -> None:
        self._context = context

    def resolve(self) -> AccessContext:
        return self._context
