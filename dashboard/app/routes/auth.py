"""认证路由:index / login / logout。

搬迁自 app.py:414-481(原 `index`/`login_form`/`login_submit`)+ 764-769(`logout`)。
"""
import logging
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from nas import NAS_BASE, encrypt_field, resolve_device_id

log = logging.getLogger("zspace-poc")
router = APIRouter()


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


@router.get("/")
async def index(request: Request):
    if request.session.get("nas_cookies"):
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: Optional[str] = None):
    return _templates(request).TemplateResponse(
        request, "login.html", {"error": error}
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    device_id = resolve_device_id()
    form = {
        "username": encrypt_field(username),
        "password": encrypt_field(password),
        "plat": "web",
        "device": "linux",
        "device_id": device_id,
    }
    log.info("login attempt user=%s device_id=%s", username, device_id)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(f"{NAS_BASE}/auth/login", data=form)
        except httpx.HTTPError as e:
            return _templates(request).TemplateResponse(
                "login.html",
                {"request": request, "error": f"NAS 连接失败: {e}"},
            )
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:500]}

    if str(body.get("code")) != "200":
        err = f"登录失败 code={body.get('code')} msg={body.get('msg')}"
        log.warning("login failed: %s", body)
        return _templates(request).TemplateResponse(
            request,
            "login.html",
            {"error": err, "raw_response": body},
        )

    data = body.get("data") or {}
    cookies: Dict[str, str] = {
        "token": data.get("token", ""),
        "username": username,
        "device_id": device_id,
        "device": "linux",
        "plat": "web",
    }
    for ck, cv in resp.cookies.items():
        cookies[ck] = cv

    request.session["nas_cookies"] = cookies
    request.session["nas_user"] = {"username": username, "profile": data}
    log.info("login ok user=%s", username)
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
