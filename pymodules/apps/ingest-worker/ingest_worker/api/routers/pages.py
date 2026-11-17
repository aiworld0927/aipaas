"""静态页面接口。"""
from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse

from aipaas_common.utils.path_util import PROJECT_ROOT

router = APIRouter(tags=["页面"])

_IMPORT_PAGE_PATH = PROJECT_ROOT / "pages" / "import.html"


@router.get("/import.html")
async def get_import_page():
    """返回文件导入前端页面。"""
    if not _IMPORT_PAGE_PATH.exists():
        raise HTTPException(status_code=404, detail=f"没有查询到页面，地址为：{_IMPORT_PAGE_PATH}")
    return FileResponse(_IMPORT_PAGE_PATH)
