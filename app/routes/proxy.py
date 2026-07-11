"""调试代理 + 健康检查 + 性能 API 路由。

搬迁自 app.py:770-781(/healthz + /api/perf)+ 1302-1326(/_proxy GET/POST)。
logout 在 routes/auth.py(更合理的归属)。
"""
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.nas_helpers import nas_get, nas_post
from app.perf import get_perf_cached

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"ok": True}


@router.get("/api/perf")
async def api_perf(request: Request):
    """性能监控 JSON(LAN SSH 读 /proc,5 秒缓存)。需登录。"""
    if not request.session.get("nas_cookies"):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return get_perf_cached()


@router.get("/_proxy")
async def proxy_get(request: Request, path: str):
    """只读调试入口:?path=/zspool/info"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_get(client, path)


@router.post("/_proxy")
async def proxy_post(request: Request, path: str = None, as_form: bool = True):
    """POST 调试用:body 是 JSON,path 走 query。默认 form,?as_form=false 用 JSON"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await nas_post(client, path or "/", body, as_form=as_form)
