"""
项目日志工具类
基于loguru实现，支持控制台/文件双输出，文件名 <前缀>_YYYYMMDD.log
特性：
1. 配置驱动：由 aipaas_common.conf.settings.load_settings 加载后调用 apply_log_config 生效
2. import 期兜底：仅控制台 INFO，保证配置加载前 logger 已可用
3. 自动路径：文件日志输出到 项目根/logs/<前缀>_YYYYMMDD.log
4. 中文友好：utf-8编码，彻底解决中文乱码
5. 异步安全：开启异步入队，支持多线程/异步场景，避免日志错乱
6. 开箱即用：项目所有模块直接导入logger即可使用
"""
import asyncio
import sys
from typing import TYPE_CHECKING

from loguru import logger as _loguru_logger

from aipaas_common.core.context import request_id_ctx_var
from aipaas_common.utils.path_util import PROJECT_ROOT

if TYPE_CHECKING:
    from aipaas_common.conf.schemas.common import LoggingSettings

# -------------------------- 日志路径（自动推导项目根） --------------------------
LOG_DIR = PROJECT_ROOT / "logs"

# -------------------------- 日志格式（彩色、结构化、易读） --------------------------
LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name: <20}</cyan>:<cyan>{function: <15}</cyan>:<cyan>{line: <4}</cyan> - "
    "<level>{message}</level>"
)
LOG_FORMAT_REQUEST_ID = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>request_id - {extra[request_id]}</magenta> | "
    "<cyan>{name: <20}</cyan>:<cyan>{function: <15}</cyan>:<cyan>{line: <4}</cyan> - "
    "<level>{message}</level>"
)


def _add_console_sink(level: str) -> None:
    _loguru_logger.add(
        sink=sys.stdout,
        level=level,
        format=LOG_FORMAT_REQUEST_ID,
        colorize=True,
        enqueue=True,
    )


def _add_file_sink(level: str, retention: str, prefix: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _loguru_logger.add(
        sink=LOG_DIR / f"{prefix}_{{time:YYYYMMDD}}.log",
        level=level,
        format=LOG_FORMAT,
        rotation="00:00",
        retention=retention,
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )


def apply_log_config(cfg: "LoggingSettings", prefix: str) -> None:
    """
    按配置重建日志 sink（由 load_settings 在配置校验通过后调用）
    :param cfg: common.logging 节点校验结果
    :param prefix: 日志文件名前缀（file_prefix 或 module_name 推导值）
    """
    _loguru_logger.remove()
    if cfg.console_enable:
        _add_console_sink(cfg.console_level.upper())
    if cfg.file_enable:
        _add_file_sink(cfg.file_level.upper(), cfg.file_retention, prefix)


# -------------------------- import 期兜底：仅控制台 INFO --------------------------
_loguru_logger.remove()
_add_console_sink("INFO")

base_logger = _loguru_logger


# 注入request_id到日志记录中
def inject_request_id(record):
    request_id = request_id_ctx_var.get()
    record["extra"]["request_id"] = request_id


# 给日志打补丁，使其支持注入request_id
logger = base_logger.patch(inject_request_id)


# -------------------------- 测试代码 --------------------------
if __name__ == '__main__':
    async def print_log(request: str):
        logger.info(f"{request}")

    async def test1():
        request_id = request_id_ctx_var.set("request_1")
        await asyncio.sleep(1)
        await print_log(f"{request_id}")

    async def test2():
        request_id = request_id_ctx_var.set("request_2")
        await asyncio.sleep(1)
        await print_log(f"{request_id}")

    async def main():
        await asyncio.gather(test1(), test2(), return_exceptions=True)

    asyncio.run(main())
    logger.info("【测试】logger.py内部调用（仅测试，业务模块调用会显示正确文件名）")
    try:
        result = 10 / 0
        logger.info(f"业务结果：{result}")
    except Exception:
        logger.exception("【测试】捕获到业务异常，输出完整堆栈信息")
