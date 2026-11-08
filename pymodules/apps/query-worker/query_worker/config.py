from dataclasses import dataclass, field
from typing import Set, Optional
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class QueryConfig:
    query_high_threshold: float = field(default_factory=lambda: os.getenv("QUERY_HIGH_LIKE", 0.65))
    query_mid_threshold:  float = field(default_factory=lambda: os.getenv("QUERY_HIGH_LIKE", 0.55))
    # -----------------------------
    # Rerank / TopK 全局常量
    # -----------------------------
    # 动态 TopK 硬上限：最多取前 N 条（<=10）
    rerank_max_topk: int = field(default_factory=lambda: os.getenv("RERANK_MAX_TOPK", 10))
    # 最小 TopK：至少保留前 N 条（>=1，且 <= RERANK_MAX_TOPK）
    rerank_min_topk: int = field(default_factory=lambda: os.getenv("RERANK_MIN_TOPK", 3)) # 总数最少条数


    # 断崖阈值（绝对，判断高分文档）
    rerank_gap_abs: float = field(default_factory=lambda: os.getenv("RERANK_GAP_ABS", 0.5))
    # 断崖阈值（相对，判断低分文档）
    rerank_gap_ratio: float = field(default_factory=lambda: os.getenv("RERANK_GAP_RATIO", 0.25))

    max_context_chars:int = field(default_factory=lambda: os.getenv("MAX_CONTEXT_CHARS", 12000))

query_config: QueryConfig = QueryConfig()


if __name__ == '__main__':
    logger.info(query_config)