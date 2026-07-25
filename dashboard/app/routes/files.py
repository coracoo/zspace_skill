"""文件 CRUD 路由(/action/{mkdir,rename,move,copy,remove,info})。

搬迁自 app.py:1327-1342, 1399-1485(mkdir / info / rename / move / copy / remove)。
"""
import logging

import httpx
from fastapi import APIRouter, Form, Request

from dashboard.app.nas_helpers import append_common_query, nas_post

from zspace.nas import NAS_BASE

log = logging.getLogger("zspace-poc")
router = APIRouter()


@router.post("/action/mkdir")
async def action_mkdir(request: Request, parent: str = Form(...), name: str = Form(...)):
    """创建文件夹:NAS /v2/file/newdir"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    log.info("mkdir received parent=%r name=%r", parent, name)
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await nas_post(client, "/v2/file/newdir", {
            "parent": parent,
            "name": name,
            "rename": 0,
        })
    log.info("mkdir parent=%s name=%s → code=%s", parent, name, res.get("code"))
    return res


@router.post("/action/info")
async def action_info(request: Request, path: str = Form(...)):
    """文件/文件夹详情:NAS /v2/file/info"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await nas_post(client, "/v2/file/info", {"path": path})
    return res


@router.post("/action/rename")
async def action_rename(request: Request, path: str = Form(...), newname: str = Form(...)):
    """改名:NAS /v2/file/modify。注意字段名是 newname,不是 name/rename"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        # 字段名 newname 是 NAS 要求,httpx data={"newname": ...} 直接传字符串就行
        url = append_common_query(f"{NAS_BASE}/v2/file/modify")
        r = await client.post(url, data={"path": path, "newname": newname})
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("rename path=%s newname=%s → code=%s", path, newname, res.get("code"))
    return res


@router.post("/action/move")
async def action_move(request: Request, paths: str = Form(...), to: str = Form(...)):
    """移动:NAS /v2/file/move。字段 paths[](PHP 数组) + to"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        url = append_common_query(f"{NAS_BASE}/v2/file/move")
        # paths 可能是逗号分隔的多个,拆成数组
        path_list = [p.strip() for p in paths.split(",") if p.strip()]
        form = {"to": to, "paths[]": path_list}  # httpx 会把 list 重复成 paths[]=a&paths[]=b
        r = await client.post(url, data=form)
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("move paths=%s to=%s → code=%s", path_list, to, res.get("code"))
    return res


@router.post("/action/copy")
async def action_copy(request: Request, paths: str = Form(...), to: str = Form(...)):
    """复制:NAS /v2/file/copy。同 move 的字段"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        url = append_common_query(f"{NAS_BASE}/v2/file/copy")
        path_list = [p.strip() for p in paths.split(",") if p.strip()]
        form = {"to": to, "paths[]": path_list}
        r = await client.post(url, data=form)
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("copy paths=%s to=%s → code=%s", path_list, to, res.get("code"))
    return res


@router.post("/action/remove")
async def action_remove(request: Request, paths: str = Form(...)):
    """删除:NAS /v2/file/remove(端点名是 remove 不是 delete)"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        url = append_common_query(f"{NAS_BASE}/v2/file/remove")
        path_list = [p.strip() for p in paths.split(",") if p.strip()]
        form = {"paths[]": path_list}
        r = await client.post(url, data=form)
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("remove paths=%s → code=%s", path_list, res.get("code"))
    return res
