"""解析选项：所有影响产物的开关都显式化，默认值面向金融标准文档。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _normalize_mode(value: Any, allowed: tuple[str, ...], field: str, default: str) -> str:
    if value is None:
        return default
    mode = str(value).strip().lower()
    if mode not in allowed:
        raise ValueError(f"{field} 仅支持 {allowed}，收到 {value!r}")
    return mode


@dataclass(slots=True)
class ParseOptions:
    """解析配置。

    - ``revision_mode``：Word 修订处理，``ignore`` 取最终稿，``keep`` 在正文保留修订标记。
    - ``comment_mode``：Word 批注处理，``ignore`` 丢弃，``keep`` 追加到所在段落并入库。
    - ``formula_mode``：Excel 公式取值，``result`` 取缓存计算值，``source`` 取公式原文。
    - ``max_retries``：解析器内部自动重试上限，P0 固定 ≤2。
    """

    revision_mode: str = "ignore"
    comment_mode: str = "ignore"
    formula_mode: str = "result"
    max_retries: int = 2
    retry_backoff_seconds: float = 0.05
    max_heading_level: int = 6

    def __post_init__(self) -> None:
        self.revision_mode = _normalize_mode(self.revision_mode, ("ignore", "keep"), "revision_mode", "ignore")
        self.comment_mode = _normalize_mode(self.comment_mode, ("ignore", "keep"), "comment_mode", "ignore")
        self.formula_mode = _normalize_mode(self.formula_mode, ("result", "source"), "formula_mode", "result")
        if not 0 <= self.max_retries <= 2:
            raise ValueError("max_retries 必须在 0..2 之间（P0 自动重试 ≤2 次）")
        if self.max_heading_level < 1:
            raise ValueError("max_heading_level 必须 ≥1")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "ParseOptions":
        if not values:
            return cls()
        allowed = {field for field in cls.__dataclass_fields__}
        filtered = {key: value for key, value in values.items() if key in allowed and value is not None}
        return cls(**filtered)
