import asyncio
import shutil
import time
import zipfile
from pathlib import Path

import httpx

from aipaas_common.conf.settings import get_settings
from aipaas_common.core.exceptions import BusinessError
from aipaas_common.log_decorator import node_log
from aipaas_common.core.log import logger
from ingest_worker.graph.state import ImportGraphState


class MinerUParseError(BusinessError):
    """MinerU 无法解析该文件（文件损坏、加密或格式不支持等），需修复后重传。"""


@node_log("node_pdf_to_md")
async def node_pdf_to_md(state: ImportGraphState):
    """PDF 转 Markdown 节点：PDF结构化解析（异步版）。"""
    pdf_path_obj, output_dir_obj = _step_1_validate_paths(state)
    zip_url = await _step_2_upload_and_poll(pdf_path_obj)
    md_path = await _step_3_download_and_extract(zip_url, output_dir_obj, pdf_path_obj.stem)
    with open(md_path, "r", encoding="utf-8") as f:
        md_content = f.read()
    state["md_path"] = md_path
    state["md_content"] = md_content
    return state


def _step_1_validate_paths(state: ImportGraphState):
    """校验 PDF 文件路径和输出目录。"""
    pdf_path = state.get("pdf_path")
    if not pdf_path:
        raise ValueError("pdf_path 参数不存在")
    file_dir = state.get("file_dir")
    if not file_dir:
        logger.warning(f"file_dir is none,file_dir={file_dir}")
        raise ValueError("file_dir 参数不存在")
    pdf_path_obj = Path(pdf_path)
    file_dir_obj = Path(file_dir)
    if not pdf_path_obj.exists():
        raise ValueError(f"PDF文件{pdf_path_obj.name}不存在")
    if not file_dir_obj.exists():
        logger.info(f"输出目录不存在，自动创建：{file_dir_obj.absolute()}")
        file_dir_obj.mkdir(parents=True, exist_ok=True)
    return pdf_path_obj, file_dir_obj


async def _step_2_upload_and_poll(pdf_path_obj: Path) -> str:
    """上传 PDF 到 MinerU 并轮询解析任务状态（异步 httpx）。"""
    async with httpx.AsyncClient(timeout=None) as client:
        # 1. 获取上传链接
        token = get_settings().common.mineru.api_token_str
        url = f"{get_settings().common.mineru.base_url}/file-urls/batch"
        header = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        data = {"files": [{"name": pdf_path_obj.name}], "model_version": "vlm"}

        response = await client.post(url, headers=header, json=data)
        if response.status_code != 200:
            raise ValueError(f"获取上传链接响应失败：状态码：{response.status_code}，响应结果：{response}")

        result = response.json()
        if result.get("code") != 0:
            raise ValueError(f"获取上传链接失败：返回数据：{result}")

        signed_url = result["data"]["file_urls"][0]
        batch_id = result["data"]["batch_id"]

        # 2. 文件上传（同步文件读入 + async PUT）
        with open(pdf_path_obj, "rb") as f:
            file_bytes = f.read()
        res_upload = await client.put(signed_url, content=file_bytes)
        if res_upload.status_code != 200:
            raise ValueError(f"文件上传失败：状态码：{res_upload.status_code}，响应结果：{res_upload}")
        logger.info("文件上传成功！")

        # 3. 轮询任务状态
        poll_url = f"{get_settings().common.mineru.base_url}/extract-results/batch/{batch_id}"
        start_time = time.time()
        timeout_seconds = 600
        poll_interval = 5
        logger.info(f"【任务轮询】最大超时：{timeout_seconds}s，batch_id：{batch_id}")

        while True:
            elapsed_time = time.time() - start_time
            if elapsed_time > timeout_seconds:
                raise TimeoutError(f"【任务轮询】超时！任务处理超{timeout_seconds}秒，batch_id：{batch_id}")

            try:
                res_poll = await client.get(poll_url, headers=header, timeout=10)
            except Exception as e:
                logger.warning(f"【任务轮询】网络请求异常，{poll_interval}s 后重试：{str(e)}，batch_id：{batch_id}")
                await asyncio.sleep(poll_interval)
                continue

            if res_poll.status_code != 200:
                raise ValueError(f"【任务轮询】HTTP请求失败，状态码：{res_poll.status_code}，响应内容：{res_poll}")

            poll_data = res_poll.json()
            if poll_data["code"] != 0:
                raise ValueError(f"【任务轮询】业务错误，返回数据：{poll_data}")

            result_item = poll_data["data"]["extract_result"][0]
            data_state = result_item["state"]

            if data_state == "done":
                logger.info(f"【任务轮询】解析任务完成！总耗时{int(elapsed_time)}s，batch_id：{batch_id}")
                full_zip_url = result_item["full_zip_url"]
                logger.info(f"【任务轮询】返回ZIP包下载链接：{full_zip_url}，batch_id：{batch_id}")
                return full_zip_url
            elif data_state == "failed":
                err_msg = result_item.get("err_msg") or "未知错误，无具体信息"
                raise MinerUParseError(
                    f"文件导入失败：MinerU 无法解析此文件，请尝试修复后重新上传"
                    f"（文件：{pdf_path_obj.name}，batch_id：{batch_id}，MinerU 返回：{err_msg}）"
                )
            else:
                logger.info(f"【任务轮询】处理中... 已耗时{int(elapsed_time)}s，状态：{data_state}，batch_id：{batch_id}")
                await asyncio.sleep(poll_interval)


async def _step_3_download_and_extract(zip_url: str, output_dir_obj: Path, pdf_stem: str) -> str:
    """下载 ZIP、解压、提取 MD 文件（下载 httpx async，解压/文件 IO to_thread）。"""
    logger.info(f"【ZIP下载】开始下载ZIP包：{zip_url} ...")
    async with httpx.AsyncClient(timeout=None) as client:
        response = await client.get(zip_url)

    if response.status_code != 200:
        raise RuntimeError(f"【ZIP下载】ZIP包下载失败：状态码：{response.status_code}，响应结果：{response}")

    zip_save_path = output_dir_obj / f"{pdf_stem}_result.zip"
    with open(zip_save_path, "wb") as f:
        f.write(response.content)
    logger.info(f"【ZIP下载】ZIP包下载成功：保存路径：{zip_save_path}")

    extract_target_dir = output_dir_obj / pdf_stem
    if extract_target_dir.exists():
        await asyncio.to_thread(shutil.rmtree, extract_target_dir)
        logger.info(f"【ZIP解压】已清空旧的解压目录：{extract_target_dir}")

    extract_target_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"【ZIP解压】开始解压ZIP包：{output_dir_obj} ...")
    await asyncio.to_thread(_extract_zip, zip_save_path, extract_target_dir)
    logger.info(f"【ZIP解压】ZIP解压完成，解压目录：{extract_target_dir}")

    logger.info("【MD重命名】找到 MinerU 生成的 full.md 文件，开始重命名")
    target_md_file = extract_target_dir / "full.md"
    new_md_path = target_md_file.with_name(f"{pdf_stem}.md")
    target_md_file.rename(new_md_path)
    logger.info(f"【MD重命名】重命名成功，文件名：{pdf_stem}.md")

    return str(new_md_path.absolute())


def _extract_zip(zip_path: Path, target_dir: Path):
    """纯文件 IO 小工具，跑在线程池里避免阻塞事件循环。"""
    with zipfile.ZipFile(zip_path, "r") as zip_file_obj:
        zip_file_obj.extractall(target_dir)


if __name__ == "__main__":
    import json
    from aipaas_common.utils.path_util import PROJECT_ROOT
    from ingest_worker.graph.state import create_default_ingest_state
    from ingest_worker.graph.nodes.a_node_entry import node_entry

    init_state = create_default_ingest_state(
        **{"import_file_path": str(PROJECT_ROOT / "data" / "inputs" / "hak180使用说明书.pdf")}
    )
    async def _test():
        node_entry_state = node_entry(init_state)  # a_node_entry 仍为 sync
        return await node_pdf_to_md(node_entry_state)

    result = asyncio.run(_test())
    logger.info(json.dumps(result, ensure_ascii=False, indent=4))
