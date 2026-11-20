# MinerU本地模型部署手册

MinerU 是一个将 PDF 高质量转换为 Markdown 的开源工具。

```python
# 官方文档：
https://opendatalab.github.io/MinerU/zh/
https://mineru.net/apiManage/docs
```

## 1. 本地部署

### 1.2 安装MinerU

```python
# 创建新项目:在当前目录下：mineru_local：
uv init mineru_local_121  --python 3.11
# uv init  # 创建新项目:在当前目录下

# 指定 Python 版本 创建venv 
uv venv --python 3.11

# 配置 pyproject.toml
[tool.uv.sources]
# 强制从nvidia源安装
torch = {index = "pytorch-cuda"}
torchvision = {index = "pytorch-cuda"}
torchaudio = {index = "pytorch-cuda"}

[[tool.uv.index]]
name = "pytorch-cuda"
url = "https://download.pytorch.org/whl/cu121"
explicit = true

[[tool.uv.index]]
url = "https://mirrors.aliyun.com/pypi/simple"

# 安装依赖包
# ===================== 环境安装命令（适配BGE-M3+Milvus，GPU/CPU版区分）=====================
# 【优先推荐-GPU版】安装CUDA 12.8版PyTorch（含torchvision/torchaudio，NVIDIA显卡GPU加速必备）
# uv add torch torchvision torchaudio --default-index https://download.pytorch.org/whl/cu128
# uv remove torch torchvision torchaudio
uv add torch torchvision torchaudio --default-index https://download.pytorch.org/whl/cu124

# 【备用-CPU版】无NVIDIA显卡（AMD/Intel集显）请用此命令，直接安装CPU版PyTorch
# uv add torch torchvision torchaudio

uv add -U "transformers"
uv add -U "shapely"

# 安装MinerU  -U 参数的作用：它告诉安装器：不仅安装这个包，还要把它以及它的依赖升级到满足条件的最新版本。
uv add -U "mineru[all]" --default-index https://mirrors.aliyun.com/pypi/simple
# uv add -U "mineru"

# 查看是否安转成功
mineru --version

"""
注意事项：
1、pytorch最后安装，避免miner CPU版本的torch 替换 GPU版本

uv add torch torchvision torchaudio "transformers" "mineru[all]"

"""


"""
    工程环境
"""
conda create -n py311 python=3.11
conda activate py311
python -m venv kb311
.\Scripts\activate

"""
    pytorch
"""
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
pip uninstall torch torchvision torchaudio -y
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126 (5090)
pip install --pre torch torchvision --index-url https://download.pytorch.org/whl/nightly/cu128 (5090)
cuda开发工具：https://developer.nvidia.com/cuda-12-4-0-download-archive

nvidia-smi
检查pytorch状态：python -c "import torch; print('CUDA Available:', torch.cuda.is_available())"

测试：
import torch
print(torch.cuda.is_available())  # 如果输出 True，说明成功认出显卡！
print(torch.version.cuda)         # 这会显示你安装的 PyTorch 自带的 CUDA 版本（比如 12.1）

"""
    mineru
"""

pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple
pip install uv -i https://mirrors.aliyun.com/pypi/simple
uv pip install -U "mineru[all]" -i https://mirrors.aliyun.com/pypi/simple
测试： mineru --version

"""
    mineru模型
"""
模型缓存：$env:MODELSCOPE_CACHE="D:\aigc\modelscope_cache\models"
下模型：mineru-models-download
桌面客户端：https://mineru.net/
测试：mineru -p xxx.pdf -o output --backend pipeline

mineru -p D:\docs\hak180产品安全手册.pdf -o output --backend pipeline
```

### 1.3 下载模型

```bash
# 第一步：设置 ModelScope 缓存路径
$env:MODELSCOPE_CACHE="D:\ai_models\modelscope_cache"
#默认下载到：C:\Users\55317\.cache\modelscope\hub\models\ 中

# 第二步：执行下载（1.选择modelscope，2.选择all）
mineru-models-download

# 第三步：修改mineru.json中的"models-dir"
D:\\ai_models\\modelscope_cache\\
```

### 1.4 使用MinerU

```bash
# 使用本地模型
$env:MINERU_MODEL_SOURCE="local"
mineru -p abc.pdf -o output --backend pipeline
```

## 2. 解析文档

在python项目中创建test.py文件

```python
"""
执行 MinerU 命令
import_file_path: 解析的文件路径
file_dir_path: 解析后的文件目录
"""
import os
import subprocess
import time

# 1. 设置环境变量
env = os.environ.copy()
env["MINERU_MODEL_SOURCE"] = "local"

# 1. 构建命令行
cmd = "mineru -p abc.pdf -o output1 --backend pipeline"

process_start_time = time.time()

# 2. 执行命令行(子进程执行命令行)
proc = subprocess.Popen(
    args=cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    errors="replace",       # 遇到乱码时替换
    text=True,              # 输出的内容是字符串 不是字节
    encoding="utf-8",       # 用指定的中文字符集进行编解码
    bufsize=1               # 按行缓冲，只要缓冲区一行满了就输出
)

# 3. 获取日志信息
for line in proc.stdout:
    print(f"执行MinerU产生的日志：{line}")

# 4. 等待子进程做完
processed_code = proc.wait()

process_end_time = time.time()
if processed_code == 0:
    print(f"MinerU成功解析PDF文件，耗时:{process_end_time - process_start_time:.2f}s")
else:
    print("MinerU解析PDF文件失败")

```

---

## 3. 整体架构概览

### 3.1 五种后端引擎对比

| 对比项           | pipeline                         | auto-engine hybrid | auto-engine vlm | http-client hybrid       | http-client vlm          |
| ---------------- | -------------------------------- | ------------------ | --------------- | ------------------------ | ------------------------ |
| **解析方法**     | 传统流水线                       | 本地混合引擎       | 本地 VLM 引擎   | 远程混合引擎             | 远程 VLM 引擎            |
| **后端特性**     | 兼容性好                         | 硬件要求较高       | 硬件要求较高    | 适用于 OpenAI 兼容服务器 | 适用于 OpenAI 兼容服务器 |
| **精度指标**     | 82+                              | 82+                | 90+             | 82+                      | 90+                      |
| **纯 CPU 支持**  | ✅                                | ✅                  | ❌               | ✅                        | ✅                        |
| **GPU 加速**     | Volta 及以后架构 / Apple Silicon | 同左               | 同左            | 不需要                   | 不需要                   |
| **显存最低要求** | 6GB                              | 10GB               | 8GB             | 3GB                      | 不需要                   |
| **内存要求**     | 最低 16GB，推荐 32GB             | 同左               | 同左            | 最低 8GB                 | 最低 8GB                 |
| **磁盘空间**     | 20GB+，推荐 SSD                  | 同左               | 同左            | 至少 2GB                 | 至少 2GB                 |
| **Python 版本**  | 3.10 - 3.13                      | 同左               | 同左            | 同左                     | 同左                     |

### 3.2 三大类引擎说明

**pipeline（传统流水线）**
最经典的模式。8 个专业模型按流水线顺序处理，兼容性最好，纯 CPU 也能跑，精度 82+，显存最低 6GB。

**auto-engine（本地自动引擎）**
模型在本地运行，分两种方法：`hybrid` 混合模式（显存 10GB，精度 82+）和 `vlm` 端到端模式（显存 8GB，精度 90+，必须有 GPU）。

**http-client（远程客户端）**
模型不在本地运行，调用远程 OpenAI 兼容 API。本地硬件要求极低（内存 8GB、磁盘 2GB），不需要 GPU。同样分 `hybrid` 和 `vlm` 两种方法。

### 3.3 VLM 引擎

`MinerU2.5-2509-1.2B` 是一个 **1.2B 参数的视觉语言模型（Vision-Language Model）**，采用端到端架构。

#### 3.3.1 工作原理

```
PDF 页面 → 渲染为图片 → VLM 模型一次性处理 → 直接输出 Markdown
```

与传统 Pipeline 不同，VLM 不需要先切块再分类，而是像一个"全能选手"一样，一次性完成版面理解、文字识别、表格还原、公式转换等所有任务。

#### 3.3.2 核心优势

- **架构简洁**：不需要 8 个模型的复杂调度，部署和维护更简单。
- **上下文感知强**：大模型能理解页面的整体语义，不会因为切块而丢失上下文。
- **持续进化**：受益于视觉语言模型的快速发展，后续版本能力提升空间大。

---

### 3.4 Pipeline 引擎 

#### 3.4.1 模型职责一览

| 模型             | 全称                          | 角色定位   | 核心职责                                             |
| ---------------- | ----------------------------- | ---------- | ---------------------------------------------------- |
| **OriCls**       | Orientation Classification    | 方向分类器 | 判断页面是否旋转（0°/90°/180°/270°），将歪斜页面摆正 |
| **Layout**       | Layout Analysis               | 版面分析   | 画包围框，识别正文、标题、插图、页眉页脚等区域       |
| **MFD**          | Math Formula Detection        | 公式检测   | 扫描数学公式位置，区分行内公式与行间公式             |
| **MFR**          | Math Formula Recognition      | 公式识别   | 将公式截图转换为标准 LaTeX 代码                      |
| **TabCls**       | Table Classification          | 表格分类   | 判断表格类型：有线表、无线表、三线表等               |
| **TabRec**       | Table Recognition             | 表格识别   | 还原行列结构和合并单元格，输出 HTML/Markdown 表格    |
| **OCR**          | Optical Character Recognition | 文字识别   | 将"普通文本"区域的像素转换为可编辑文本               |
| **ReadingOrder** | Reading Order                 | 阅读顺序   | 决定所有区块的排列顺序，处理分栏、图文混排           |

#### 3.4.2 处理流程（以复杂 PDF 页面为例）

**步骤 1：预处理与纠正**

```
PDF 页面 → 渲染为高清图片 → OriCls 检测方向 → 旋转摆正
```

**步骤 2：版面切割（定位区域）**

```
摆正后的页面图片
    ├── Layout  → 识别：段落、标题、图片、页眉页脚、表格等区块
    └── MFD     → 识别：行内公式、行间公式的位置
```

两个模型并行工作，将页面切成若干带标签的小方块。

**步骤 3：分类处理（各显神通）**

流水线在此处**分叉**，不同标签的区块交给对应的专家：

```
标签为「段落/标题」的区块  ──→  OCR      ──→  输出纯文本
标签为「公式」的区块        ──→  MFR      ──→  输出 LaTeX 代码
标签为「表格」的区块        ──→  TabCls   ──→  判断表格类型
                                  └──→  TabRec   ──→  输出 Markdown/HTML 表格
标签为「图片」的区块        ──→  直接提取保存为图片文件
```

**步骤 4：排序重组（最终输出）**

```
所有提取结果（文本 + LaTeX + 表格 + 图片引用）
    └── ReadingOrder → 按正确的阅读顺序排列 → 拼接为最终 Markdown
```
