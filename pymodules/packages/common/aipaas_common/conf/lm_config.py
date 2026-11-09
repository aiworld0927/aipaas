# 导入核心依赖：数据类、环境变量读取、路径处理
from dataclasses import dataclass
import os
from dotenv import load_dotenv

# 提前加载.env配置文件（必须在读取环境变量前执行，确保os.getenv能获取到值）
# 若.env不在项目根目录，可指定路径：load_dotenv(dotenv_path=Path(__file__).parent / ".env")
load_dotenv()


# 定义minerU服务配置
@dataclass
class LLMConfig:
    base_url: str
    api_key : str
    vl_model: str
    llm_model: str
    llm_temperature: float
    mcp_base_url: str
    mcp_api_key: str

lm_config = LLMConfig(
    base_url=os.getenv("OPENAI_BASE_URL","https://dashscope.aliyuncs.com/compatible-mode/v1"),
    api_key=os.getenv("OPENAI_API_KEY",""),
    vl_model=os.getenv("VL_MODEL","qwen3.6-flash"),
    llm_model=os.getenv("LLM_DEFAULT_MODEL",""),
    llm_temperature=float(os.getenv("LLM_DEFAULT_TEMPERATURE",0.0)),
    mcp_base_url=os.getenv("MCP_SERVER_BASE_URL",""),
    mcp_api_key=os.getenv("OPENAI_API_KEY",""),
)

if __name__ == '__main__':
    print(lm_config)