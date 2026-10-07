# 导入核心依赖：数据类、环境变量读取、路径处理
from dataclasses import dataclass
import os
from dotenv import load_dotenv

# 提前加载.env配置文件（保持和原代码一致，只需执行一次）
load_dotenv()

# 定义Embedding配置（适配BGE-M3的所有配置，类名embedding_config）
@dataclass
class EmbeddingConfig:
    bge_m3_path: str  # 本地模型路径
    bge_m3: str       # 模型仓库标识
    bge_device: str   # 运行设备(cuda:0/cpu)
    bge_fp16: bool    # 是否开启半精度（1=True/0=False）

# 实例化配置对象，和原代码lm_config风格保持一致
embedding_config = EmbeddingConfig(
    bge_m3_path=os.getenv("BGE_M3_PATH",""),
    bge_m3=os.getenv("BGE_M3",""),
    bge_device=os.getenv("BGE_DEVICE",""),
    # 特殊处理：将.env中的1/0转为布尔值，兼容常见的数字/字符串格式
    bge_fp16=os.getenv("BGE_FP16") in ("1", "True", "true", 1)
)

"""
半精度推理（FP16）是指将原本32位的浮点数运算转换为16位运算。
📌可以这样类比：
- 全精度（FP32）：就像用一把非常精确的尺子，能测量到小数点后很多位，精度高但占空间、速度慢
- 半精度（FP16）：就像用一把稍微粗糙一点的尺子，精度稍低但更轻便、更快

📌具体区别：
- FP32 = 32位浮点数 = 单精度 = 占用4字节内存
- FP16 = 16位浮点数 = 半精度 = 占用2字节内存

📌使用半精度的好处：
- 速度更快：GPU处理16位数据比32位快得多
- 显存更少：模型占用内存减半，可以处理更大的批次
- 功耗更低：计算量减少，更省电

📌可能的影响：
- 精度略有损失，但对于大多数AI推理任务来说，这种损失几乎察觉不到
- 特别适合像BGE-M3这样的嵌入模型，对精度要求不是特别苛刻的场景
所以BGE_FP16=True 就是让BGE-M3模型使用16位半精度进行推理，以获得更快的速度和更低的内存占用。
"""