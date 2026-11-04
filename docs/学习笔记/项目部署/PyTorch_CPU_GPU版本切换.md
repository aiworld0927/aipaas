# PyTorch CPU / GPU 版本切换说明

适用项目：`pymodules`（uv 管理）。一套配置兼容两台机器：GPU 开发机（RTX 4070）与单位纯 CPU 机器。

## 1. 设计原理

`pyproject.toml` 通过 `override-dependencies` 把 PyTorch 三件套**锁死为 cu128 构建**：

```toml
[tool.uv]
override-dependencies = [
    "torch==2.11.0+cu128",
    "torchaudio==2.11.0+cu128",
    "torchvision==0.26.0+cu128",
]

[tool.uv.sources]
torch = [{ index = "pytorch-cuda128" }]
torchvision = [{ index = "pytorch-cuda128" }]
torchaudio = [{ index = "pytorch-cuda128" }]
```

效果：**任何 `uv sync` / `uv run` / PyCharm 自动同步，都只会安装 cu128 构建**，GPU 机不会被意外降级为 CPU 版。锁文件 `uv.lock` 中 torch 只有 `2.11.0+cu128` 一个变体。

代价：CPU 机器无法通过 sync 直接得到 CPU 版，需手动覆盖（见下文）。

## 2. 行为速查表

| 命令 | 结果 |
|---|---|
| `uv sync` | 强制收敛为 `2.11.0+cu128`（GPU 版） |
| `uv run python xxx.py` | 先执行上面的同步，再运行 → 永远是 GPU 版 |
| `uv run --no-sync python xxx.py` | 不同步，venv 里是什么就用什么 |
| 激活 venv 后直接 `python xxx.py` | 不经过 uv，venv 里是什么就用什么 |
| PyCharm 用 `.venv\Scripts\python.exe` 作解释器运行 | 不经过 uv，venv 里是什么就用什么 |

> 关键认知：cu128（GPU 版）torch 跑纯 CPU 代码完全正常，只有"体积大"这一个缺点。

## 3. GPU 版 → 切换为 CPU 版
 
**先 `cd pymodules`**（命令里的 `.\.venv` 是相对路径，在仓库根目录执行会因找不到解释器而失败），然后执行单行命令（避免多行反引号续行在粘贴时变形）：

```powershell
uv pip install --no-config --python .\.venv\Scripts\python.exe torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 --reinstall --index-url https://mirrors.aliyun.com/pypi/simple
```

成功的标志是输出里出现 `- torch==2.11.0+cu128` / `+ torch==2.11.0` 的替换行；**`Checked 3 packages` 之类输出 = 什么都没换**。

三个参数缺一不可：

| 参数 | 作用 | 缺少的后果 |
|---|---|---|
| `--no-config` | 绕过项目里 override 的 cu128 锁定 | 请求被改写为 `==2.11.0+cu128`，在 PyPI 镜像上解析失败报错 |
| `--reinstall` | 强制重装 | `==2.11.0` 按 PEP 440 会被已装的 `2.11.0+cu128` 满足，uv 静默跳过（`Checked 3 packages`，什么都没换） |
| `--python .\.venv\Scripts\python.exe` | 明确装进项目 venv | conda base 激活时可能装进 conda 环境，venv 纹丝不动 |

> 多行写法（反引号 `` ` `` 续行）要求反引号是每行**最后一个字符**，从文档/微信复制常带入尾部空格导致续行断裂、命令被拆成多条执行。粘贴后若看到"不是可识别的命令"类报错，即为此问题——用上面的单行写法最稳。

验证（**必须用 venv 的 python 直接验证**，预期输出 `2.11.0+cpu / None / False`）：

```powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
```

> PyPI（及阿里云镜像）的 Windows torch wheel 文件名是 plain `2.11.0`，但内部版本号即 `2.11.0+cpu`，为纯 CPU 构建。若需官方 `download.pytorch.org/whl/cpu` 源，把 `--index-url` 换掉即可，命令其余部分不变。

## 4. CPU 版 → 切回 GPU 版

```powershell
uv sync
```

一条命令即可（cu128 wheel 通常已在本地缓存，秒级完成；首次则需下载约 2.5GB）。

验证（预期输出 `2.11.0+cu128 / 12.8 / True`）：

```powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
```

或运行检测脚本：

```powershell
.\.venv\Scripts\python.exe tests\03-cuda测试.py
```

## 5. 两台机器的标准流程

### GPU 开发机（本机）

```powershell
uv sync                      # 初始化/恢复，永远得到 cu128 GPU 版
python xxx.py                # 激活 venv 后直接运行即可
```

PyCharm 解释器直接指向 `pymodules\.venv\Scripts\python.exe`，随意运行不会触发依赖变更。

### 单位 CPU 机器

```powershell
# 首次：跳过 torch 三件套同步基础依赖，避免白下 2.5GB cu128 包
uv sync --no-install-package torch --no-install-package torchaudio --no-install-package torchvision

# 再覆盖安装 CPU 构建（第 3 节的单行命令）
uv pip install --no-config --python .\.venv\Scripts\python.exe torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 --reinstall --index-url https://mirrors.aliyun.com/pypi/simple
```

之后运行脚本用激活 venv 的 `python xxx.py` 或 `uv run --no-sync python xxx.py`。

**单位机禁忌**：不要再执行裸 `uv sync` / `uv run`，会把 CPU 版恢复成 cu128；误执行后重跑上面的覆盖命令即可。

## 6. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 执行安装命令后还是 cu128 | 缺 `--reinstall`，被 PEP 440 版本相等规则静默跳过 | 补 `--reinstall` 重跑 |
| 报错 `torch was found ... but not at the requested version (torch==2.11.0+cu128)` | 缺 `--no-config`，override 把请求改写成了 cu128 | 补 `--no-config` 重跑 |
| 装了但 venv 里版本没变 | 缺 `--python`，装进了 conda base | 补 `--python .\.venv\Scripts\python.exe` 重跑 |
| 单位机突然变回 cu128 | 执行了裸 `uv sync` / `uv run` | 重跑第 3 节覆盖命令 |
| 想用 `uv pip list` 确认版本 | conda base 激活时会误读 base 环境 | 改用 `.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__)"` |
