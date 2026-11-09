from pathlib import Path
from aipaas_common.log_decorator import node_log
from ingest_worker.graph.state import ImportGraphState


@node_log("node_entry")
def node_entry(state: ImportGraphState):
    """入口节点：任务分发
        1.接收状态: 获取 `import_file_path`。
        2.判断类型: 检查文件后缀是 `.pdf` 还是 `.md`。
        3.设置标记: 更新 state 中的 `is_pdf_read_enabled/pdf_path` 或 `is_md_read_enabled/md_path`，供主图路由使用。
        4.提取标题: 从文件名中提取 `file_title`，后续作为元数据。
        :param state: `import_file_path`
        :return: `is_pdf_read_enabled/pdf_path` 或 `is_md_read_enabled/md_path` 、`file_title`
    """

    # 1. 从state中获取文件
    import_file_path = state.get("import_file_path")
    # import_file_path = state["import_file_path"] #这样写的话字段不存在直接报错
    # 判断路径是否为空
    if not import_file_path:
        raise ValueError(f"import_file_path 参数不存在")

    # 2. 转换Path标准化对象
    import_file_path_obj = Path(import_file_path)
    # 判断文件是否存在
    if not import_file_path_obj.exists():
        raise ValueError(f"文件{import_file_path_obj.name}不存在")

    # 3. 检查文件后缀
    if import_file_path_obj.suffix == ".pdf":
        state["is_pdf_read_enabled"] = True
        state["pdf_path"] = import_file_path
    elif import_file_path_obj.suffix == ".md":
        state["is_md_read_enabled"] = True
        state["md_path"] = import_file_path
    else:
        raise ValueError(f"该文件的后缀格式{import_file_path_obj.suffix}不支持")

    # 4. 获取上传文件的标题，更新到state中
    state["file_title"] = import_file_path_obj.stem

    # 5. 返回state
    return state


if __name__ == "__main__":
    import json
    from aipaas_common.core.log import logger
    from aipaas_common.utils.path_util import PROJECT_ROOT
    from ingest_worker.graph.state import create_default_ingest_state
    # init_state = create_default_ingest_state(**{"import_file_path": str(PROJECT_ROOT / "data" / "inputs" / "H3CLA2608室内无线网关用户手册.pdf")})
    init_state = create_default_ingest_state(**{"import_file_path": str(PROJECT_ROOT / "data" / "inputs" / "hak180使用说明书.pdf")})
    result = node_entry(init_state)
    logger.info(json.dumps(result, ensure_ascii=False, indent=4))
