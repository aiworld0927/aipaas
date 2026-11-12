import asyncio
import base64
import mimetypes
import os
import re
from collections import deque
from pathlib import Path
from typing import Dict, Tuple, List

from minio import Minio
from minio.deleteobjects import DeleteObject

from aipaas_common.core.log import logger
from aipaas_common.log_decorator import node_log

from aipaas_common.conf.settings import get_settings
from aipaas_common.utils.rate_limit_utils import apply_api_rate_limit
from aipaas_model_clients.lm.lm_utils import get_llm_client
from aipaas_storage.minio_utils import get_minio_client

from ingest_worker.graph.state import ImportGraphState


@node_log("node_md_img")
async def node_md_img(state: ImportGraphState):
    """MarkDown图片处理节点：多模态图片理解（异步版）。

    LLM 调用走 ainvoke，MinIO 同步 driver 调用全部 asyncio.to_thread 包。
    """
    md_content, md_path_obj, images_dir = _step_1_get_content(state)
    if not images_dir.exists():
        logger.info("无图片文件夹，跳过图片处理")
        return state

    target_images = _step_2_scan_images(md_content, images_dir)
    if not target_images:
        logger.info("未检测到MD中引用了图片，跳过图片处理")
        return state

    summaries = await _step_3_generate_summaries(md_path_obj.stem, target_images)
    new_md_content = await _step_4_upload_and_replace(md_path_obj.stem, target_images, summaries, md_content)
    new_md_file_name = _step_5_backup_new_md_file(state["md_path"], new_md_content)

    state["md_content"] = new_md_content
    state["md_path"] = new_md_file_name
    return state


def _step_1_get_content(state: ImportGraphState) -> Tuple[str, Path, Path]:
    md_path = state.get("md_path")
    if not md_path:
        raise ValueError("md_path 参数不存在")
    md_path_obj = Path(md_path)
    if not md_path_obj.exists():
        raise ValueError(f"MD文件{md_path_obj.name}不存在")
    md_content = state["md_content"]
    images_dir = md_path_obj.parent / "images"
    return md_content, md_path_obj, images_dir


def _step_2_scan_images(md_content: str, images_dir: Path) -> List[Tuple[str, str, Tuple[str, str]]]:
    """扫描图片文件夹，过滤出「支持格式 + MD 中实际引用」的图片。"""
    target_images = []
    for image_file in os.listdir(images_dir):
        file_ext = os.path.splitext(image_file)[1].lower()
        if file_ext not in get_settings().module.image_extensions:
            logger.warning(f"图片格式不支持，跳过：{image_file}")
            continue
        img_path = str(images_dir / image_file)
        context = _find_image_in_md(md_content, image_file)
        if not context:
            logger.warning(f"图片未在MD中引用，跳过处理：{image_file}")
            continue
        target_images.append((image_file, img_path, context))
    return target_images


def _find_image_in_md(md_content: str, image_file: str, context_len: int = 100):
    pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_file) + r".*?\)")
    match = pattern.search(md_content)
    if not match:
        return None
    start, end = match.span()
    pre_text = md_content[max(0, start - context_len):start]
    post_text = md_content[end:min(len(md_content), end + context_len)]
    return pre_text, post_text


async def _step_3_generate_summaries(doc_stem: str, target_images: List) -> Dict[str, str]:
    """批量为待处理图片生成摘要（LLM ainvoke + asyncio.to_thread 包 base64 文件读）。"""
    summaries = {}
    request_deque = deque()
    for index, (img_file, image_path, context) in enumerate(target_images):
        apply_api_rate_limit(request_deque, max_requests=10)
        summaries[img_file] = await _summarize_image(image_path, root_folder=doc_stem, image_content=context)
        logger.info(f"图片处理进度:{index} / {len(target_images)}: 当前处理：{img_file}")
    return summaries


async def _summarize_image(image_path: str, root_folder: str, image_content: Tuple[str, str]) -> str:
    """调用多模态大模型总结图片内容（ainvoke，async 模型 IO 只让事件循环）。"""
    image_path_obj = Path(image_path)
    base64_image = await asyncio.to_thread(lambda: base64.b64encode(image_path_obj.read_bytes()).decode("utf-8"))

    try:
        chat_model = get_llm_client(get_settings().common.llm.vl_model)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"""这是"{root_folder}"文件中的一张图片，图片上文部分为"{image_content[0]}"，下文部分为"{image_content[1]}"，请用中文简要总结这张图片的内容，用于 Markdown 图片标题。"""},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mimetypes.guess_type(image_path_obj.name)[0]};base64,{base64_image}"
                        },
                    },
                ],
            }
        ]
        response = await chat_model.ainvoke(messages)
        return response.content.strip().replace("\n", "")
    except Exception as e:
        logger.error(f"图像总结失败：{image_path}, 错误{e}")
        return "图片描述"


async def _step_4_upload_and_replace(doc_stem: str, target_images: List, summaries: Dict[str, str], md_content: str) -> str:
    """MinIO 上传 + MD 内容替换。MinIO 是同步 driver，全部调用放线程池。"""
    minio_client = await asyncio.to_thread(get_minio_client)
    minio_img_dir = get_settings().common.minio.img_dir
    upload_dir = f"{minio_img_dir}/{doc_stem}".replace(" ", "")

    await asyncio.to_thread(_clean_minio_directory, minio_client, upload_dir)
    urls = await asyncio.to_thread(_upload_images_batch, minio_client, upload_dir, target_images)

    image_info = _merge_summary_and_url(summaries, urls)
    md_content = _process_md_file(md_content, image_info)
    return md_content


def _clean_minio_directory(minio_client: Minio, prefix: str) -> None:
    try:
        objects_to_delete = minio_client.list_objects(get_settings().common.minio.bucket_name, prefix=prefix, recursive=True)
        delete_list = [DeleteObject(obj.object_name) for obj in objects_to_delete]
        if delete_list:
            errors = minio_client.remove_objects(get_settings().common.minio.bucket_name, delete_list)
            for error in errors:
                logger.error(f"删除失败：{error}")
    except Exception as e:
        logger.error(f"清理minio目录失败：{e}")


def _upload_images_batch(minio_client: Minio, upload_dir: str, target_images: List[Tuple]) -> Dict[str, str]:
    urls = {}
    for img_file, img_path, _ in target_images:
        object_name = f"{upload_dir}/{img_file}"
        urls[img_file] = _upload_to_minio(minio_client, img_path, object_name)
    return urls


def _upload_to_minio(minio_client: Minio, local_path: str, object_name: str):
    try:
        minio_client.fput_object(
            bucket_name=get_settings().common.minio.bucket_name,
            object_name=object_name,
            file_path=local_path,
            content_type=f"image/{os.path.splitext(local_path)[1][1:]}",
        )
        minio_cfg = get_settings().common.minio
        base_url = f"{minio_cfg.base_url}/{minio_cfg.bucket_name}"
        return f"{base_url}/{object_name}"
    except Exception as e:
        logger.error(f"图片上传MinIO失败：{local_path}，错误信息：{str(e)}")
        return None


def _merge_summary_and_url(summaries: Dict[str, str], urls: Dict[str, str]) -> Dict[str, Tuple[str, str]]:
    image_info = {}
    for image_file, summary in summaries.items():
        if url := urls.get(image_file):
            image_info[image_file] = (summary, url)
    return image_info


def _process_md_file(md_content: str, image_info: Dict[str, Tuple[str, str]]) -> str:
    for image_file, (summary, new_url) in image_info.items():
        pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_file) + r".*?\)")
        md_content = pattern.sub(lambda m: f"![{summary}]({new_url})", md_content)
    logger.info(f"MD文件图片引用替换完成，共替换{len(image_info)}处图片引用")
    return md_content


def _step_5_backup_new_md_file(origin_md_path: str, md_content: str) -> str:
    new_md_file_name = os.path.splitext(origin_md_path)[0] + "_new.md"
    with open(new_md_file_name, "w", encoding="utf-8") as f:
        f.write(md_content)
    logger.info(f"处理后MD文件已保存，新文件路径：{new_md_file_name}")
    return new_md_file_name


if __name__ == "__main__":
    import json
    from ingest_worker.graph.state import create_default_ingest_state
    from aipaas_common.utils.path_util import PROJECT_ROOT

    async def _test():
        md_path = str(PROJECT_ROOT / "data" / "outputs" / "hak180使用说明书/hak180使用说明书.md")
        with open(md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
        init_state = create_default_ingest_state(md_path=md_path, md_content=md_content)
        return await node_md_img(init_state)

    result = asyncio.run(_test())
    logger.info(json.dumps(result, ensure_ascii=False, indent=4))
