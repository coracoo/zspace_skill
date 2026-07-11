"""极影视分类路由(/action/{add-classification,link-folder})。

搬迁自 app.py:1344-1399。
"""
import logging

import httpx
from fastapi import APIRouter, Form, Request

from app.nas_helpers import append_common_query, nas_post

from nas import NAS_BASE

log = logging.getLogger("zspace-poc")
router = APIRouter()


@router.post("/action/add-classification")
async def action_add_classification(
    request: Request,
    classification_name: str = Form(...),
    file_path: str = Form(""),
    not_scrape: int = Form(1),
):
    """建极影视分类:NAS /zvideo/classification/add"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    form = {
        "classification_name": classification_name,
        "share_users": "[]",
        "not_scrape": not_scrape,
    }
    if file_path:
        form["file_path"] = file_path
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await nas_post(client, "/zvideo/classification/add", form)
    log.info("add-classification name=%s file_path=%s → code=%s",
             classification_name, file_path, res.get("code"))
    return res


@router.post("/action/link-folder")
async def action_link_folder(
    request: Request,
    classification_id: str = Form(...),
    file_path: str = Form(...),
):
    """把目录关联到极影视分类:NAS /zvideo/classification/increase
    注意:字段名是 file_path[](PHP 数组语法),httpx 直接传 dict 会编码成 file_path%5B%5D,
    后端 PHP 解析时还原成 file_path 数组。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    form = {
        "classification_id": classification_id,
        "file_path[]": file_path,  # PHP 数组语法,关键!
    }
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        # 不能用 nas_post(它 dict→form 时可能丢 [] 字段),直接打
        url = append_common_query(f"{NAS_BASE}/zvideo/classification/increase")
        r = await client.post(url, data=form)
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("link-folder classification=%s file_path=%s → code=%s",
             classification_id, file_path, res.get("code"))
    return res
