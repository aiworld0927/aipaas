# 导入核心依赖：数据类、环境变量读取、路径处理
from dataclasses import dataclass
import os
from dotenv import load_dotenv

# 提前加载.env配置文件（保持和原代码一致，只需执行一次）
load_dotenv()

@dataclass
class RerankerConfig:
    bge_reranker_large: str  # 本地模型路径
    bge_reranker_device: str       # 模型仓库标识
    bge_reranker_fp16: bool    # 是否开启半精度（1=True/0=False）

    # 在线rerank模型
    text_rerank_api_key: str # DashScope API Key
    text_rerank_model: str # 模型名称
    text_rerank_instruct: str # 是否使用指令

# 实例化配置对象，和原代码lm_config风格保持一致
reranker_config = RerankerConfig(
    bge_reranker_large=os.getenv("BGE_RERANKER_LARGE",""),
    bge_reranker_device=os.getenv("BGE_RERANKER_DEVICE",""),
    # 特殊处理：将.env中的1/0转为布尔值，兼容常见的数字/字符串格式
    bge_reranker_fp16=os.getenv("BGE_RERANKER_FP16") in ("1", "True", "true", 1),

    # 在线rerank模型
    text_rerank_api_key = os.getenv("OPENAI_API_KEY",""),
    text_rerank_model = os.getenv("TEXT_RERANK_MODEL",""),
    text_rerank_instruct = os.getenv("TEXT_RERANK_INSTRUCT","")
)
if __name__ == '__main__':
    print(reranker_config)