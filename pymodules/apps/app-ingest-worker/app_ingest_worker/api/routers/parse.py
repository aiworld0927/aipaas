"""文档解析与分块接口：解析、解析结果、清洗结果、分块预览与查询。

解析器按格式注册、可插拔；每次解析都会生成一个新版本产物（对象存储）并覆盖
同一文档的 chunk 集合，因此重复解析/重试不会产生重复 chunk。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from aipaas_common.core.log import logger
from aipaas_pipeline.chunking import ChunkConfig
from aipaas_pipeline.cleaning import CleaningOptions
from aipaas_pipeline.options import ParseOptions
from app_ingest_worker.api.dependencies import ParseServiceDep

router = APIRouter(prefix="/api/v1", tags=["文档解析"])


class ChunkPreviewRequest(BaseModel):
    """分块预览参数：与正式分块共用同一套配置与构建逻辑。"""

    model_config = ConfigDict(populate_by_name=True)

    strategy: str = Field("structured", description="分块策略：fixed/structured/itemized")
    size: int = Field(800, description="固定分块窗口大小（token）")
    overlap: int = Field(100, description="固定分块重叠窗口（token）")
    sentence_aligned: bool = Field(True, alias="sentenceAligned", description="按句边界对齐")
    include_code_blocks: bool = Field(True, alias="includeCodeBlocks", description="结构化分块包含代码块")
    include_tables: bool = Field(True, alias="includeTables", description="结构化分块包含表格")
    limit: int = Field(50, description="预览返回的 chunk 上限（≤500）")


@router.post(
    "/documents/{document_id}:parse",
    summary="解析文档",
    description="上传文档字节并同步解析（PDF/Word/Excel/Markdown），返回统一中间结构",
)
async def parse_document(
    document_id: str,
    service: ParseServiceDep,
    file: UploadFile = File(..., description="文档文件"),
    fmt: str | None = Form(None, alias="format", description="显式指定格式，默认取扩展名"),
    standard_no: str | None = Form(None, alias="standardNo", description="标准编号提示"),
    tenant_id: str = Form("default", alias="tenantId", description="租户 ID"),
    revision_mode: str = Form("ignore", alias="revisionMode", description="修订处理：ignore/keep"),
    comment_mode: str = Form("ignore", alias="commentMode", description="批注处理：ignore/keep"),
    formula_mode: str = Form("result", alias="formulaMode", description="公式取值：result/source"),
    max_retries: int = Form(2, alias="maxRetries", description="解析器内部自动重试次数（≤2）"),
    clean_enabled: bool = Form(True, alias="cleanEnabled", description="是否执行 #14 数据清洗"),
    clean_rules: str | None = Form(None, alias="cleanRules", description="清洗规则 JSON（可选覆盖）"),
    chunk_strategy: str = Form("structured", alias="chunkStrategy",
                               description="分块策略：fixed/structured/itemized"),
    chunk_size: int = Form(800, alias="chunkSize", description="固定分块窗口大小（token）"),
    chunk_overlap: int = Form(100, alias="chunkOverlap", description="固定分块重叠窗口（token）"),
    sentence_aligned: bool = Form(True, alias="sentenceAligned", description="固定分块按句边界对齐"),
    chunk_include_code: bool = Form(True, alias="chunkIncludeCodeBlocks", description="结构化分块包含代码块"),
    chunk_include_tables: bool = Form(True, alias="chunkIncludeTables", description="结构化分块包含表格"),
):
    """解析上传的文档，返回中间结构（含条款、章节树、表格、告警与版本号）。"""
    try:
        options = ParseOptions.from_mapping(
            {
                "revision_mode": revision_mode,
                "comment_mode": comment_mode,
                "formula_mode": formula_mode,
                "max_retries": max_retries,
            }
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": str(exc), "data": None},
        )
    try:
        rules = json.loads(clean_rules) if clean_rules else {}
        if not isinstance(rules, dict):
            raise ValueError("cleanRules 必须是 JSON 对象")
        clean_options = CleaningOptions.from_mapping({**rules, "enabled": clean_enabled})
    except (ValueError, json.JSONDecodeError) as exc:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": f"清洗规则不合法：{exc}", "data": None},
        )
    try:
        chunk_options = ChunkConfig(
            strategy=chunk_strategy,
            size=chunk_size,
            overlap=chunk_overlap,
            sentence_aligned=sentence_aligned,
            include_code_blocks=chunk_include_code,
            include_tables=chunk_include_tables,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": f"分块参数不合法：{exc}", "data": None},
        )

    data = await file.read()
    document = await asyncio.to_thread(
        service.parse,
        data,
        doc_id=document_id,
        tenant_id=tenant_id,
        filename=file.filename or "",
        fmt=fmt,
        standard_no=standard_no,
        options=options,
        clean_options=clean_options,
        chunk_options=chunk_options,
    )
    logger.info(
        f"解析接口完成 document={document_id} version={document.version} "
        f"success={document.success} failureCode={document.failure_code}"
    )
    return {
        "code": 200,
        "message": "解析成功" if document.success else f"解析失败：{document.failure_code}",
        "data": document.to_payload(),
    }


@router.get(
    "/documents/{document_id}/parse-result",
    summary="查询最新解析结果",
)
async def get_parse_result(document_id: str, service: ParseServiceDep):
    """返回指定文档的最新解析结果（不存在时 404）。"""
    payload = service.get_result(document_id)
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": "解析结果不存在", "data": None},
        )
    return {"code": 200, "message": "ok", "data": payload}


@router.get(
    "/documents/{document_id}/parse-result/versions",
    summary="查询解析版本列表",
)
async def list_parse_versions(document_id: str, service: ParseServiceDep):
    """返回该文档已落库的全部解析版本号（升序）。"""
    return {
        "code": 200,
        "message": "ok",
        "data": {"documentId": document_id, "versions": service.list_versions(document_id)},
    }


@router.get(
    "/documents/{document_id}/parse-result/clean",
    summary="查询最新清洗结果",
)
async def get_clean_result(document_id: str, service: ParseServiceDep):
    """返回清洗后的统一中间结构（不覆盖原始解析结果）。"""
    payload = service.get_clean_result(document_id)
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": "清洗结果不存在", "data": None},
        )
    return {"code": 200, "message": "ok", "data": payload}


@router.get(
    "/documents/{document_id}/parse-result/clean-diff",
    summary="查询清洗差异报告",
)
async def get_clean_diff(document_id: str, service: ParseServiceDep):
    """返回清洗前后差异（规则、摘要、逐字段变更与保护统计）。"""
    payload = service.get_clean_diff(document_id)
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": "清洗差异不存在", "data": None},
        )
    return {"code": 200, "message": "ok", "data": payload}


@router.post(
    "/documents/{document_id}:chunk-preview",
    summary="预览分块边界",
    description="按指定策略与参数预览分块结果，不写入任何产物；与正式分块共用同一构建逻辑",
)
async def preview_chunks(document_id: str, request: ChunkPreviewRequest, service: ParseServiceDep):
    """返回分块预览（含 token 数、hash、条款锚点与边界偏移）。"""
    try:
        config = ChunkConfig(
            strategy=request.strategy,
            size=request.size,
            overlap=request.overlap,
            sentence_aligned=request.sentence_aligned,
            include_code_blocks=request.include_code_blocks,
            include_tables=request.include_tables,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": f"分块参数不合法：{exc}", "data": None},
        )
    payload = service.preview_chunks(document_id, config, limit=request.limit)
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": "解析结果不存在", "data": None},
        )
    return {"code": 200, "message": "ok", "data": payload}


@router.get(
    "/documents/{document_id}/chunks",
    summary="查询当前分块结果",
)
async def get_chunks(document_id: str, service: ParseServiceDep):
    """返回最新版本落库的 chunk 集合与本次分块参数（含配置版本）。"""
    payload = service.get_chunks_artifact(document_id)
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": "分块结果不存在", "data": None},
        )
    return {"code": 200, "message": "ok", "data": payload}


@router.get(
    "/documents/{document_id}/chunk-config",
    summary="查询分块参数版本",
)
async def get_chunk_config(document_id: str, service: ParseServiceDep):
    """返回最新版本的分块参数、配置版本与断言结果。"""
    payload = service.get_chunk_config(document_id)
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": "分块配置不存在", "data": None},
        )
    return {"code": 200, "message": "ok", "data": payload}


@router.get(
    "/documents/{document_id}/clauses",
    summary="条目化条款清单（导出与溯源复用）",
)
async def list_clauses(
    document_id: str,
    service: ParseServiceDep,
    page: int = 1,
    page_size: int = 100,
    tenant_id: str | None = None,
):
    """返回条款编号、标题、正文、所属章节、页码与 chunkId，支持分页。"""
    if tenant_id:
        document = service.get_document(document_id)
        if document is None:
            return JSONResponse(
                status_code=404,
                content={"code": 404, "message": "解析结果不存在", "data": None},
            )
        if document.tenant_id and document.tenant_id != tenant_id:
            return JSONResponse(
                status_code=403,
                content={"code": 403, "message": "跨租户访问被拒绝", "data": None},
            )
    payload = service.list_clauses(document_id, page=page, page_size=page_size)
    if payload is None:
        return JSONResponse(
            status_code=404,
            content={"code": 404, "message": "解析结果不存在", "data": None},
        )
    return {"code": 200, "message": "ok", "data": payload}
