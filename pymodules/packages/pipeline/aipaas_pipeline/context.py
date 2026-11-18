"""解析上下文：一次解析所需的全部外部输入。"""

from __future__ import annotations

from dataclasses import dataclass, field

from aipaas_pipeline.options import ParseOptions


@dataclass(slots=True)
class ParseContext:
    doc_id: str
    tenant_id: str = "default"
    filename: str = ""
    format: str = ""
    standard_no_hint: str | None = None
    options: ParseOptions = field(default_factory=ParseOptions)
