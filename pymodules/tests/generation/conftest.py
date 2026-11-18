"""生成链路测试夹具：条款样本、权限快照与供应商/检索器假实现。"""

from __future__ import annotations

import pytest

from aipaas_generation import LLMClientError, RetrievedClause
from helpers import DemoAccess


@pytest.fixture
def access() -> DemoAccess:
    return DemoAccess()


@pytest.fixture
def clauses() -> list[RetrievedClause]:
    return [
        RetrievedClause(
            chunk_id="chunk-1",
            document_id="doc-1",
            kb_id="kb-1",
            standard_no="JR/T 0088",
            standard_name="个人金融信息保护技术规范",
            clause_no="4.1.1",
            chapter_path="4 技术要求 > 4.1 安全管理",
            text="金融机构应当建立个人金融信息风险评估机制，每年至少评估一次。",
            score=0.92,
            rank=1,
        ),
        RetrievedClause(
            chunk_id="chunk-2",
            document_id="doc-1",
            kb_id="kb-1",
            standard_no="JR/T 0088",
            standard_name="个人金融信息保护技术规范",
            clause_no="4.1.2",
            chapter_path="4 技术要求 > 4.1 安全管理",
            text="金融机构不得收集与业务无关的个人金融信息。",
            score=0.81,
            rank=2,
        ),
        RetrievedClause(
            chunk_id="chunk-3",
            document_id="doc-2",
            kb_id="kb-1",
            standard_no="JR/T 0197",
            clause_no="5.2.1",
            chapter_path="5 数据安全 > 5.2 存储",
            text="个人金融信息存储应当采用加密措施。",
            score=0.63,
            rank=3,
        ),
    ]


@pytest.fixture
def timeout_error() -> LLMClientError:
    return LLMClientError("供应商超时", code="MODEL_TIMEOUT", retryable=True)
