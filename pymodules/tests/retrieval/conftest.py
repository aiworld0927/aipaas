from __future__ import annotations

import math

import pytest

from aipaas_retrieval import (
    AccessContext,
    InMemoryAuditSink,
    InMemoryVectorSearcher,
    RetrievalConfig,
    RetrievalService,
    VectorRecord,
)


class TopicEncoder:
    """按主题关键词映射到固定维度的确定性编码器，用于断言语义相关性。"""

    dimension = 4
    model = "TOPIC-TEST"

    TOPICS = (
        ("风险评估", (1.0, 0.0, 0.0, 0.0)),
        ("客户身份", (0.0, 1.0, 0.0, 0.0)),
        ("数据安全", (0.0, 0.0, 1.0, 0.0)),
        ("个人信息", (0.0, 0.0, 1.0, 0.0)),
        ("反洗钱", (0.0, 0.0, 0.0, 1.0)),
        ("尽职调查", (0.0, 0.0, 0.0, 1.0)),
    )

    def encode(self, text: str) -> list[float]:
        vector = [0.01, 0.01, 0.01, 0.01]
        for keyword, weights in self.TOPICS:
            if keyword in text:
                vector = [value + weight for value, weight in zip(vector, weights)]
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def build_records(encoder: TopicEncoder) -> list[VectorRecord]:
    def record(**kwargs) -> VectorRecord:
        values = {
            "tenant_id": "default",
            "security_level": "PUBLIC",
            "issuer": "中国人民银行",
            "domain": "信息安全",
            "effective_at": "2017-06-01",
        }
        values.update(kwargs)
        text = values["text"]
        return VectorRecord(vector=encoder.encode(text), **values)

    return [
        record(
            chunk_id="a-4.1.1",
            document_id="doc-88",
            kb_id="kb_a",
            text="4.1.1 金融机构应当建立个人金融信息风险评估机制，定期开展风险评估。",
            clause_no="4.1.1",
            clause_title="风险评估",
            chapter_path="第四章 个人金融信息处理 > 4.1 一般规定",
            page_no=12,
            standard_no="JR/T 0088-2016",
            standard_name="个人金融信息保护技术规范",
        ),
        record(
            chunk_id="a-4.1.2",
            document_id="doc-88",
            kb_id="kb_a",
            text="4.1.2 金融机构应建立客户身份识别制度，核验客户有效身份证件。",
            clause_no="4.1.2",
            clause_title="客户身份识别",
            chapter_path="第四章 个人金融信息处理 > 4.1 一般规定",
            standard_no="JR/T 0088-2016",
            standard_name="个人金融信息保护技术规范",
        ),
        record(
            chunk_id="a-5.1",
            document_id="doc-88",
            kb_id="kb_a",
            text="5.1 数据安全与个人信息保护：个人金融信息应加密存储并脱敏展示。",
            clause_no="5.1",
            chapter_path="第五章 数据安全",
            standard_no="JR/T 0088-2016",
            standard_name="个人金融信息保护技术规范",
        ),
        record(
            chunk_id="a-4.2",
            document_id="doc-88",
            kb_id="kb_a",
            text="4.2 内部风险评估报告仅限内部使用，不得对外提供。",
            clause_no="4.2",
            chapter_path="第四章 个人金融信息处理 > 4.2 内部管理",
            standard_no="JR/T 0088-2016",
            standard_name="个人金融信息保护技术规范",
            security_level="INTERNAL",
        ),
        record(
            chunk_id="b-5.1",
            document_id="doc-197",
            kb_id="kb_b",
            text="5.1 反洗钱客户尽职调查与可疑交易报告要求。",
            clause_no="5.1",
            chapter_path="第五章 反洗钱义务",
            standard_no="JR/T 0197-2020",
            standard_name="金融数据安全 数据安全分级指南",
            domain="反洗钱",
            effective_at="2020-10-01",
        ),
        record(
            chunk_id="c-3.2",
            document_id="doc-35273",
            kb_id="kb_c",
            text="3.2 数据安全要求：个人信息处理应遵循最小必要原则。",
            clause_no="3.2",
            chapter_path="第三章 数据安全要求",
            standard_no="GB/T 35273-2020",
            standard_name="信息安全技术 个人信息安全规范",
            issuer="国家市场监督管理总局",
            domain="数据安全",
            effective_at="2025-09-01",
        ),
        # 未授权知识库：即使密级为 PUBLIC 也不得在召回阶段出现
        record(
            chunk_id="x-1.2",
            document_id="doc-x",
            kb_id="kb_forbidden",
            text="1.2 未授权知识库中的风险评估条款。",
            clause_no="1.2",
            standard_no="JR/T 0088-2016",
            standard_name="个人金融信息保护技术规范",
        ),
        # 密级超出普通用户上限
        record(
            chunk_id="x-1.1",
            document_id="doc-x",
            kb_id="kb_forbidden",
            text="1.1 绝密风险评估材料。",
            clause_no="1.1",
            standard_no="JR/T 0088-2016",
            security_level="SECRET",
        ),
    ]


@pytest.fixture()
def encoder() -> TopicEncoder:
    return TopicEncoder()


@pytest.fixture()
def records(encoder: TopicEncoder) -> list[VectorRecord]:
    return build_records(encoder)


@pytest.fixture()
def searcher(records: list[VectorRecord]) -> InMemoryVectorSearcher:
    store = InMemoryVectorSearcher()
    store.upsert(records)
    return store


@pytest.fixture()
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


@pytest.fixture()
def service(searcher: InMemoryVectorSearcher, encoder: TopicEncoder, audit: InMemoryAuditSink):
    instance = RetrievalService(
        searcher=searcher,
        encoder=encoder,
        audit=audit,
        config=RetrievalConfig(timeout_ms=300),
    )
    yield instance
    instance.close()


def access(
    *,
    user_id: str = "u1",
    permissions: set[str] | None = None,
    kb_ids: tuple[str, ...] = ("kb_a", "kb_b", "kb_c"),
    max_level: str = "PUBLIC",
    all_kbs: bool = False,
) -> AccessContext:
    permission_set = frozenset(permissions if permissions is not None else {"retrieval:query"})
    kb_permissions = {kb: frozenset({"retrieval:query"}) for kb in kb_ids}
    return AccessContext(
        user_id=user_id,
        permissions=permission_set,
        kb_permissions=kb_permissions,
        all_kbs=all_kbs,
        max_security_level=max_level,
    )


@pytest.fixture()
def make_access():
    """权限快照工厂：``make_access(kb_ids=..., max_level=...)``。"""
    return access
