"""ZSpace NAS MCP PoC - 本地登录代理 + 数据获取验证"""
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("zspace-poc")

NAS_BASE = "http://192.168.0.135:5055"
NAS_PUBKEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtrDHnaRmRaMAhZC2CmRV
CPO3ekJRo5ELX3Jjtr9P8MoWHSQbsAE5G+VTkKWhTyMQQMR0erKabn82fOZgyOO4
F+CVRSJH0TRD854IeQyFD2iZg2W2J/BzYNYC8EmBjlRhs8oS5LBc0WUN7bP4et0s
Z2LGSXbt6TetSndeV9LP8+zaKka+xvV/9aohg5rc5Ha5ka7BfTliBOyzLPR+UTKe
mx9ysWrXedlYGUjXkDRyp4xfj98bOx44EmswJh+YHYNSINyCZ4nMsat98aWOPEDl
jsflEvNt6vXFDqrziOjAPW0S/wvyvrFCZxlb+IxJMrtNH7M61spGfobE8sjNU+MC
wwIDAQAB
-----END PUBLIC KEY-----"""

# 复用一个已登记的设备 device_id(从 NAS device.db 只读查到),
# 避免 "新设备需要短信验证" 流程。
# 默认值:PC (Firefox/151.0),user_id=1 名下最近一次 web 登录的设备。
NAS_DEVICE_ID_DEFAULT = "a6b4bd9ea4839ab4aea6f22b558bf0b2"

SESSION_SECRET = "zspace-mcp-poc-dev-only-not-for-prod"

_PUBKEY = load_pem_public_key(NAS_PUBKEY_PEM)


def encrypt_field(plain: str) -> str:
    """RSA-PKCS1v15 + base64. 服务端 Plugin_Util::transDecode 的逆操作."""
    cipher = _PUBKEY.encrypt(plain.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(cipher).decode("ascii")


def resolve_device_id() -> str:
    """优先用环境变量 NAS_DEVICE_ID,否则用代码里默认值。始终 32 字符。"""
    did = os.environ.get("NAS_DEVICE_ID", "").strip()
    return did if (len(did) == 32) else NAS_DEVICE_ID_DEFAULT


app = FastAPI(title="ZSpace NAS MCP PoC")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/")
async def index(request: Request):
    if request.session.get("nas_cookies"):
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(
        request, "login.html", {"error": error}
    )


@app.post("/login")
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
            return templates.TemplateResponse(
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
        return templates.TemplateResponse(
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


async def _nas_get(client: httpx.AsyncClient, path: str) -> Dict[str, Any]:
    try:
        url = f"{NAS_BASE}{path}"
        url = _append_common_query(url)
        r = await client.get(url)
        try:
            return r.json()
        except Exception:
            return {"_status": r.status_code, "_raw": r.text[:300]}
    except Exception as e:
        return {"_error": str(e)}


async def _nas_post(
    client: httpx.AsyncClient, path: str, body: Any, as_form: bool = True
) -> Dict[str, Any]:
    """默认用 form-urlencoded(pcweb 默认),body 是 dict 自动展开。"""
    try:
        url = _append_common_query(f"{NAS_BASE}{path}")
        if as_form and isinstance(body, dict):
            r = await client.post(url, data=body)
        else:
            r = await client.post(url, json=body)
        try:
            return r.json()
        except Exception:
            return {"_status": r.status_code, "_raw": r.text[:300]}
    except Exception as e:
        return {"_error": str(e)}


def _append_common_query(url: str) -> str:
    """axios 拦截器给所有请求追加的公共参数(pcweb 默认行为)"""
    sep = "&" if "?" in url else "?"
    return (
        f"{url}{sep}plat=web"
        f"&version=2.3.2026062201"
        f"&device_id={NAS_DEVICE_ID_DEFAULT}"
        f"&device=linux"
        f"&_l=zh-CN"
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return RedirectResponse("/login", status_code=303)

    import asyncio
    file_path = request.query_params.get("path") or "/sata14/my/data/"
    if not file_path.startswith("/"):
        file_path = "/" + file_path
    if not file_path.endswith("/"):
        file_path = file_path + "/"
    breadcrumb = build_breadcrumb(file_path)

    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        # 所有 NAS 调用并发跑(原本串行 ~1.5s,并发后约等于最慢一个)
        monitor_html, zspool_info, zspool_hw, file_resp, zvideo_classes, zvideo_dirs = (
            await asyncio.gather(
                client.get(_append_common_query(f"{NAS_BASE}/zstatus")),
                _nas_get(client, "/zspool/info"),
                _nas_get(client, "/zspool/hardware/info"),
                _nas_post(client, "/v2/file/list", {
                    "folderId": 0,
                    "path": file_path,
                    "start": 0,
                    "num": 200,
                    "sortby": "name",
                    "order": "asc",
                    "show_hidden": 0,
                }),
                _nas_post(client, "/zvideo/classification/list", {}),
                _nas_post(client, "/zvideo/classification/dirs", {}),
            )
        )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": request.session.get("nas_user", {}),
            "monitor": parse_zstatus(monitor_html.text),
            "zspool_info": zspool_info,
            "zspool_hw": zspool_hw,
            "file_path": file_path,
            "file_resp": file_resp,
            "breadcrumb": breadcrumb,
            "zvideo_classes": zvideo_classes,
            "zvideo_dirs": zvideo_dirs,
            "cookies_keys": list(cookies.keys()),
        },
    )


def build_breadcrumb(path: str) -> list:
    """把 /sata14/my/data/foo/ 拆成 [{name, path}, ...] 用于面包屑导航。"""
    parts = [p for p in path.split("/") if p]
    crumbs = [{"name": "🏠 全部", "path": "/"}]
    acc = ""
    for p in parts:
        acc += "/" + p
        crumbs.append({"name": p, "path": acc + "/"})
    return crumbs


def parse_zstatus(html: str) -> Dict[str, Any]:
    """从 /zstatus HTML 状态页里抽关键字段。"""
    import re
    # 去 style/script
    txt = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<script[^>]*>.*?</script>", "", txt, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", "|", txt)
    txt = re.sub(r"&nbsp;|&#160;", " ", txt)
    txt = re.sub(r"\|+", "|", txt)
    parts = [p.strip() for p in txt.split("|") if p.strip()]
    s = "|".join(parts)

    def find_after(key: str, n: int = 1) -> str:
        m = re.search(re.escape(key) + r"\|([^|]+(?:\|[^|]+){" + str(n - 1) + r"})", s)
        return m.group(1) if m else ""

    out: Dict[str, Any] = {}
    # 设备状态:设备|时间|开机时长|序列号 → 时间, 开机时长, 序列号
    m = re.search(r"设备状态.*?(\d{4}-\d{2}-\d{2}[ \d:]+)\|([^|]+天[^|]*)\|([^|]+Y4H|[^|]{5,12})", s)
    if m:
        out["now"] = m.group(1).strip()
        out["uptime"] = m.group(2).strip()
        out["sn"] = m.group(3).strip()
    # 负载|内存占用 → 负载值|内存百分比
    m = re.search(r"负载\|内存占用\|([\d./]+)\|([\d.]+)％", s)
    if m:
        out["loadavg"] = m.group(1).strip()
        out["mem_pct"] = m.group(2).strip()
    # 磁盘:每行 "设备名|使用率|只读"
    disks = []
    for m in re.finditer(r"([^|]+?)\|([\d.]+)％\|(是|否)\|", s):
        name = m.group(1).strip()
        if name in ("设备", "使用率", "只读", "磁盘"):
            continue
        disks.append({"name": name, "usage_pct": m.group(2), "readonly": m.group(3)})
    out["disks"] = disks
    # 进程:进程名|运行中|健康
    procs = []
    for m in re.finditer(r"([^|]+?(?:服务|进程|组件|器|引擎))\|(是|否)\|(是|否)\|", s):
        procs.append({"name": m.group(1).strip(), "running": m.group(2), "healthy": m.group(3)})
    out["processes"] = procs
    return out


def fmt_bytes(n: Any) -> str:
    """把字节数(可能是字符串)格式化成人类可读。"""
    try:
        b = float(n)
    except Exception:
        return str(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if b < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} EB"


def datetime_local(ts: Any) -> str:
    """Unix 时间戳(秒)→ 本地时间字符串。"""
    try:
        from datetime import datetime
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


templates.env.filters["fmt_bytes"] = fmt_bytes
templates.env.filters["datetime_local"] = datetime_local


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


# ---------- 调试用通用代理(用当前 session 的 NAS cookie 直接调任意 NAS 接口)----------
@app.get("/_proxy")
async def proxy_get(request: Request, path: str):
    """只读调试入口:?path=/zspool/info"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_get(client, path)


@app.post("/_proxy")
async def proxy_post(request: Request, path: str = None):
    """POST 调试用:body 是 JSON,path 走 query"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, path or "/", body)
