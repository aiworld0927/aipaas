# -*- coding: utf-8 -*-
"""
评估程序(eval.py):基于 ragas 对 RAG 系统进行端到端评估
====================================================================
【目标】
    基于 eval/qa.csv 评估测试集,评估 RAG 查询流程的检索与生成质量。

【输入】
    eval/qa.csv(UTF-8 BOM 编码),列头:
        - question     : 测试问题(带前缀,如"HAK 180 烫金机:...")
        - ground_truth : 参考答案

【评估指标】(使用 ragas)
    Faithfulness、Answer Relevancy、Context Precision、Context Recall、Answer Correctness

【评估流程】
    step_1 加载评估集 -> step_2 初始化评估模型(LLM/嵌入均复用项目工具)
    -> step_3 逐条执行 RAG 查询流程(main_graph.query_app.invoke)
    -> step_4 从 state.reranked_docs 提取 context
    -> step_5 组装 ragas 评估数据集 -> step_6 执行 ragas 评估
    -> step_7 保存评估结果

【输出】
    eval/eval_result.csv(UTF-8 BOM 编码),列头:
        question、contexts、answer、ground_truth、Faithfulness、
        Answer Relevancy、Context Precision、Context Recall、Answer Correctness

【运行方式】
    python eval/eval.py
====================================================================
"""

# =============================================================================
# 0. 标准库导入与环境预置
# =============================================================================
import json
import sys
import types
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 屏蔽第三方库弃用告警(ragas 0.4.x 的 evaluate/Langchain*Wrapper 等标注弃用但仍可正常使用)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# -----------------------------------------------------------------------------
# [兼容性处理] 必须在导入 ragas 之前执行:
# langchain-community 0.4.x 已移除 chat_models.vertexai 模块(迁移至独立包),
# 而 ragas 0.4.3 仍从该路径导入 ChatVertexAI(仅用于 LLM 类型的 isinstance 判断)。
# 此处注入占位模块,保证 ragas 正常导入;占位类不参与实际评估逻辑,不影响评估结果。
# -----------------------------------------------------------------------------
try:
    import langchain_community.chat_models.vertexai  # noqa: F401
except ImportError:
    _vertexai_shim = types.ModuleType("langchain_community.chat_models.vertexai")

    class _ChatVertexAIPlaceholder:
        """ChatVertexAI 占位类:仅满足 ragas 的导入与 isinstance 检查,无实际功能"""

        pass

    _vertexai_shim.ChatVertexAI = _ChatVertexAIPlaceholder
    sys.modules["langchain_community.chat_models.vertexai"] = _vertexai_shim

# -----------------------------------------------------------------------------
# 项目根目录加入 sys.path(eval.py 位于 eval/ 子目录,保证 app 包可正常导入)
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent  # eval/ 目录
PROJECT_ROOT = BASE_DIR.parent  # 项目根目录
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# =============================================================================
# 1. 依赖导入
# =============================================================================
import pandas as pd
from langchain_core.embeddings import Embeddings

# 项目内部依赖
from app.agents.query_process.main_graph import query_app  # 查询流程入口(query_app.invoke)
from app.agents.query_process.state import create_default_query_state  # 查询状态构造器
from app.core.logger import logger  # 统一日志
from app.lm.embedding_utils import generate_embeddings  # 嵌入模型工具(BGE-M3)
from app.lm.lm_utils import get_llm_client  # 大模型客户端工具

# ragas 评估依赖
from ragas import evaluate  # ragas 批量评估入口
from ragas.dataset_schema import EvaluationDataset  # ragas 评估数据集
from ragas.embeddings import LangchainEmbeddingsWrapper  # 嵌入模型适配器
from ragas.llms import LangchainLLMWrapper  # LLM 适配器
from ragas.metrics import (  # 五个评估指标
    AnswerCorrectness,
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    Faithfulness,
)
from ragas.run_config import RunConfig  # ragas 运行配置(超时/并发/重试)

# =============================================================================
# 2. 常量配置
# =============================================================================
QA_FILE = BASE_DIR / "qa.csv"  # 评估测试集路径(UTF-8 BOM)
RESULT_FILE = BASE_DIR / "eval_result.csv"  # 评估结果输出路径(UTF-8 BOM)

QA_QUESTION_COLUMN = "question"  # 测试集:问题列头
QA_GROUND_TRUTH_COLUMN = "ground_truth"  # 测试集:参考答案列头

# ragas 指标结果列名(小写) -> 输出 CSV 列头 映射
METRIC_COLUMN_MAPPING = {
    "faithfulness": "Faithfulness",
    "answer_relevancy": "Answer Relevancy",
    "context_precision": "Context Precision",
    "context_recall": "Context Recall",
    "answer_correctness": "Answer Correctness",
}

# 评估结果 CSV 的列顺序
RESULT_COLUMNS = [
    "question",
    "contexts",
    "answer",
    "ground_truth",
    "Faithfulness",
    "Answer Relevancy",
    "Context Precision",
    "Context Recall",
    "Answer Correctness",
]

# ragas 运行配置参数
RAGAS_TIMEOUT = 180  # 单项指标计算超时时间(秒)
RAGAS_MAX_WORKERS = 4  # 并发工作线程数
RAGAS_MAX_RETRIES = 3  # 单项指标失败重试次数


# =============================================================================
# 3. 嵌入模型适配器
# =============================================================================
class BgeM3EmbeddingsAdapter(Embeddings):
    """
    作用:将项目内 BGE-M3 嵌入工具(app.lm.embedding_utils)适配为 LangChain Embeddings 接口,
        供 ragas 的 LangchainEmbeddingsWrapper 调用(评估指标计算需要嵌入模型)。
    """

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        作用:批量文本向量化
        :param texts: 文本列表
        :return: 稠密向量列表(与输入一一对应,模型已做 L2 归一化)
        """
        result = generate_embeddings(texts)
        return result["dense"]

    def embed_query(self, text: str) -> List[float]:
        """
        作用:单条查询文本向量化
        :param text: 查询文本
        :return: 稠密向量(单条)
        """
        result = generate_embeddings([text])
        return result["dense"][0]


# =============================================================================
# 4. 核心步骤函数(step_N_xx)
# =============================================================================
def step_1_load_qa_dataset(qa_path: Path = QA_FILE) -> pd.DataFrame:
    """
    作用:[步骤1] 加载并校验评估测试集
    步骤:
        1、以 utf-8-sig(UTF-8 BOM)编码读取 qa.csv
        2、校验列头必须包含 question / ground_truth
        3、剔除空值行后返回
    :param qa_path: 评估集文件路径
    :return: 校验通过后的评估集 DataFrame
    """
    logger.info(f"[step_1] 开始加载评估测试集:{qa_path}")
    if not qa_path.exists():
        raise FileNotFoundError(f"[step_1] 评估集文件不存在:{qa_path}")

    # 读取 UTF-8 BOM 编码的 CSV
    qa_df = pd.read_csv(qa_path, encoding="utf-8-sig")

    # 校验列头完整性
    missing_columns = [
        column
        for column in (QA_QUESTION_COLUMN, QA_GROUND_TRUTH_COLUMN)
        if column not in qa_df.columns
    ]
    if missing_columns:
        raise ValueError(
            f"[step_1] 评估集缺少必需列:{missing_columns},实际列头:{list(qa_df.columns)}"
        )

    # 仅保留目标列并剔除空值行
    qa_df = qa_df[[QA_QUESTION_COLUMN, QA_GROUND_TRUTH_COLUMN]].dropna().reset_index(drop=True)
    logger.success(f"[step_1] 评估集加载完成,共 {len(qa_df)} 条样本")
    return qa_df


def step_2_init_evaluators() -> Tuple[LangchainLLMWrapper, LangchainEmbeddingsWrapper]:
    """
    作用:[步骤2] 初始化 ragas 评估所需的模型(LLM + 嵌入),均复用项目现有工具
    步骤:
        1、LLM:调用 lm_utils.get_llm_client() 获取 ChatOpenAI 客户端,用 ragas 的 LangchainLLMWrapper 包装
        2、嵌入:基于 embedding_utils 的 BGE-M3 适配器,用 ragas 的 LangchainEmbeddingsWrapper 包装
        3、返回两个评估模型实例
    :return: (评估LLM, 评估嵌入模型)
    """
    logger.info("[step_2] 开始初始化 ragas 评估模型(LLM + 嵌入)")

    # 1、LLM 评估器:复用项目 LLM 客户端工具
    llm_client = get_llm_client()
    evaluator_llm = LangchainLLMWrapper(llm_client)

    # 2、嵌入评估器:复用项目 BGE-M3 嵌入工具(经 LangChain Embeddings 接口适配)
    evaluator_embeddings = LangchainEmbeddingsWrapper(BgeM3EmbeddingsAdapter())

    logger.success("[step_2] ragas 评估模型初始化完成")
    return evaluator_llm, evaluator_embeddings


def step_3_run_query_pipeline(question: str, session_id: str) -> Dict[str, Any]:
    """
    作用:[步骤3] 执行 RAG 查询流程,获取流程结束后的完整状态
    步骤:
        1、基于 create_default_query_state 构造初始状态(session_id + original_query)
        2、调用 main_graph.py 编译出的 query_app.invoke() 执行完整查询图
        3、返回最终状态(含 answer / reranked_docs 等)
    :param question: 测试问题
    :param session_id: 会话标识(评估用独立会话,避免历史污染)
    :return: 查询流程结束后的最终状态
    """
    logger.info(f"[step_3] 执行查询流程:{question[:50]}")
    state = create_default_query_state(
        session_id=session_id,
        original_query=question,
        is_stream=False,
    )
    result_state = query_app.invoke(state)
    return result_state


def step_4_extract_answer_and_contexts(state: Dict[str, Any]) -> Tuple[str, List[str]]:
    """
    作用:[步骤4] 从最终状态中提取答案与检索上下文
    步骤:
        1、answer:直接取 state["answer"]
        2、contexts:从 state["reranked_docs"](重排序后的最终 Top-K 文档)逐条提取 text 字段
    :param state: 查询流程结束后的最终状态
    :return: (答案, 上下文列表)
    """
    # 1、提取最终答案
    answer = state.get("answer", "") or ""

    # 2、从 reranked_docs 提取上下文文本
    reranked_docs = state.get("reranked_docs", []) or []
    contexts: List[str] = []
    for doc in reranked_docs:
        text = doc.get("text")
        if text:
            contexts.append(text)

    logger.info(f"[step_4] 提取完成:answer 长度={len(answer)},context 条数={len(contexts)}")
    return answer, contexts


def step_5_build_evaluation_dataset(records: List[Dict[str, Any]]) -> EvaluationDataset:
    """
    作用:[步骤5] 组装 ragas 评估数据集
    字段映射:
        user_input         <- question(测试问题)
        retrieved_contexts <- contexts(检索上下文)
        response           <- answer(RAG系统回答)
        reference          <- ground_truth(参考答案)
    :param records: 评估记录列表,每条含 question/ground_truth/answer/contexts
    :return: ragas 评估数据集实例
    """
    samples: List[Dict[str, Any]] = []
    for record in records:
        samples.append(
            {
                "user_input": record["question"],
                "retrieved_contexts": record["contexts"],
                "response": record["answer"],
                "reference": record["ground_truth"],
            }
        )
    dataset = EvaluationDataset.from_list(samples)
    logger.success(f"[step_5] ragas 评估数据集组装完成,样本数:{len(samples)}")
    return dataset


def step_6_run_ragas_evaluation(
    dataset: EvaluationDataset,
    evaluator_llm: LangchainLLMWrapper,
    evaluator_embeddings: LangchainEmbeddingsWrapper,
) -> pd.DataFrame:
    """
    作用:[步骤6] 使用 ragas 执行五项指标评估
    步骤:
        1、定义五个评估指标:Faithfulness / Answer Relevancy / Context Precision / Context Recall / Answer Correctness
        2、调用 ragas.evaluate() 批量计算(LLM 与嵌入均复用项目现有工具)
        3、结果转为 DataFrame,并将指标列重命名为输出要求的列头
    :param dataset: ragas 评估数据集
    :param evaluator_llm: ragas LLM 评估器
    :param evaluator_embeddings: ragas 嵌入评估器
    :return: 含五项指标得分的评估结果 DataFrame
    """
    # 1、定义评估指标
    metrics = [
        Faithfulness(),
        AnswerRelevancy(),
        ContextPrecision(),
        ContextRecall(),
        AnswerCorrectness(),
    ]

    # 2、运行配置:超时/并发/重试
    run_config = RunConfig(
        timeout=RAGAS_TIMEOUT,
        max_workers=RAGAS_MAX_WORKERS,
        max_retries=RAGAS_MAX_RETRIES,
    )

    # 3、执行 ragas 评估
    logger.info("[step_6] 开始 ragas 评估(5 项指标)...")
    result = evaluate(
        dataset=dataset,
        metrics=metrics,
        llm=evaluator_llm,
        embeddings=evaluator_embeddings,
        run_config=run_config,
        show_progress=True,
        raise_exceptions=False,
    )

    # 4、结果转 DataFrame,并把指标列名重命名为输出列头
    result_df = result.to_pandas()
    result_df = result_df.rename(columns=METRIC_COLUMN_MAPPING)
    logger.success("[step_6] ragas 评估完成")
    return result_df


def step_7_save_result_csv(result_df: pd.DataFrame, output_path: Path = RESULT_FILE) -> None:
    """
    作用:[步骤7] 保存评估结果为 CSV 文件
    步骤:
        1、按输出要求组装列:question / contexts / answer / ground_truth / 五项指标
        2、contexts 列表转为 JSON 字符串(保持结构,避免破坏 CSV 格式)
        3、以 utf-8-sig(UTF-8 BOM)编码保存到 eval/eval_result.csv
    :param result_df: ragas 评估结果 DataFrame
    :param output_path: 结果文件输出路径
    """
    logger.info(f"[step_7] 开始保存评估结果:{output_path}")

    # 1、按输出要求组装结果列
    output_df = pd.DataFrame(
        {
            "question": result_df["user_input"],
            "contexts": result_df["retrieved_contexts"].apply(_contexts_to_json_str),
            "answer": result_df["response"],
            "ground_truth": result_df["reference"],
        }
    )

    # 2、补齐五项指标列(防御性处理:缺失的指标列以空值填充并告警)
    for column in METRIC_COLUMN_MAPPING.values():
        if column in result_df.columns:
            output_df[column] = result_df[column]
        else:
            logger.warning(f"[step_7] 评估结果中缺少指标列:{column},以空值填充")
            output_df[column] = None

    # 3、按规定的列顺序输出为 UTF-8 BOM 编码的 CSV
    output_df = output_df[RESULT_COLUMNS]
    output_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    logger.success(f"[step_7] 评估结果保存完成,共 {len(output_df)} 行:{output_path}")


def _contexts_to_json_str(contexts: Any) -> str:
    """
    作用:将上下文列表序列化为 JSON 字符串(辅助函数,Csv 单元格保持结构可解析)
    :param contexts: 上下文列表(或单条文本/None)
    :return: JSON 字符串
    """
    if isinstance(contexts, (list, tuple)):
        return json.dumps(list(contexts), ensure_ascii=False)
    if contexts is None:
        return json.dumps([], ensure_ascii=False)
    return json.dumps([str(contexts)], ensure_ascii=False)


# =============================================================================
# 5. 主流程入口
# =============================================================================
def main() -> None:
    """
    作用:评估程序主入口,串联完整评估流程
    步骤:
        1、加载评估测试集(step_1)
        2、初始化评估模型(step_2)
        3、逐条执行 RAG 查询流程并收集 answer / contexts(step_3 + step_4)
        4、组装 ragas 评估数据集(step_5)
        5、执行 ragas 五项指标评估(step_6)
        6、保存评估结果到 eval/eval_result.csv(step_7)
    """
    logger.info("=" * 60)
    logger.info("RAG 评估程序启动")
    logger.info("=" * 60)

    # 1、加载评估测试集
    qa_df = step_1_load_qa_dataset(QA_FILE)

    # 2、初始化评估模型(LLM + 嵌入)
    evaluator_llm, evaluator_embeddings = step_2_init_evaluators()

    # 3、逐条执行 RAG 查询流程,收集评估记录
    #    会话命名:eval_时间戳_序号,保证多次运行互不干扰、各问题历史互不污染
    run_tag = datetime.now().strftime("%Y%m%d%H%M%S")
    records: List[Dict[str, Any]] = []
    for index, row in qa_df.iterrows():
        question = str(row[QA_QUESTION_COLUMN])
        ground_truth = str(row[QA_GROUND_TRUTH_COLUMN])
        session_id = f"eval_{run_tag}_{index + 1:03d}"
        try:
            state = step_3_run_query_pipeline(question, session_id)
            answer, contexts = step_4_extract_answer_and_contexts(state)
        except Exception as error:
            # 单条问题执行失败不中断整体评估,以空答案/空上下文记录,交由 ragas 记 NaN
            logger.error(f"[main] 第 {index + 1} 条问题执行失败:{error}", exc_info=True)
            answer, contexts = "", []
        records.append(
            {
                "question": question,
                "ground_truth": ground_truth,
                "answer": answer,
                "contexts": contexts,
            }
        )
        logger.info(f"[main] 查询进度:{index + 1}/{len(qa_df)}")

    # 4、组装 ragas 评估数据集
    dataset = step_5_build_evaluation_dataset(records)

    # 5、执行 ragas 五项指标评估
    result_df = step_6_run_ragas_evaluation(dataset, evaluator_llm, evaluator_embeddings)

    # 6、保存评估结果(UTF-8 BOM)
    step_7_save_result_csv(result_df, RESULT_FILE)

    logger.info("=" * 60)
    logger.success(f"RAG 评估全部完成,结果文件:{RESULT_FILE}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
