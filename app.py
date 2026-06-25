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
        # N150 低性能设备,并发最多 2 路,避免把 NAS 拖垮
        sem = asyncio.Semaphore(2)

        async def _g(coro):
            async with sem:
                return await coro

        # 6 个 NAS 调用,限流 2 并发跑(原本串行 ~1.5s,2 路并发约 700ms)
        monitor_html, zspool_info, zspool_hw, file_resp, zvideo_classes, zvideo_dirs = (
            await asyncio.gather(
                _g(client.get(_append_common_query(f"{NAS_BASE}/zstatus"))),
                _g(_nas_get(client, "/zspool/info")),
                _g(_nas_get(client, "/zspool/hardware/info")),
                _g(_nas_post(client, "/v2/file/list", {
                    "folderId": 0,
                    "path": file_path,
                    "start": 0,
                    "num": 200,
                    "sortby": "name",
                    "order": "asc",
                    "show_hidden": 0,
                })),
                _g(_nas_post(client, "/zvideo/classification/list", {})),
                _g(_nas_post(client, "/zvideo/classification/dirs", {})),
            )
        )

    # 写测试状态:看 test 文件夹和 test 分类是否存在
    test_dir_exists = False
    if file_resp.get("code") == "200":
        for it in (file_resp.get("data", {}).get("list") or []):
            if it.get("name") == "test" and it.get("is_dir") == "1":
                # 但当前 file_path 可能不在 备份/ 下,需要看完整 path
                if it.get("path", "").endswith("/备份/test"):
                    test_dir_exists = True
                    break
    # 单独查 备份/ 一下,因为 dashboard 的 file_path 可能是其他目录
    if not test_dir_exists:
        async with httpx.AsyncClient(timeout=8, cookies=cookies) as client:
            bak_resp = await _nas_post(client, "/v2/file/list", {
                "folderId": 0,
                "path": "/sata14/my/data/备份/",
                "start": 0, "num": 50,
                "sortby": "name", "order": "asc",
                "show_hidden": 0,
            })
        if bak_resp.get("code") == "200":
            for it in (bak_resp.get("data", {}).get("list") or []):
                if it.get("name") == "test":
                    test_dir_exists = True
                    break

    test_class_exists = False
    if zvideo_classes.get("code") == "200":
        for c in (zvideo_classes.get("data") or []):
            if c.get("name") == "test":
                test_class_exists = True
                break

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
            "test_dir_exists": test_dir_exists,
            "test_class_exists": test_class_exists,
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
        return await _nas_post(client, path or "/", body, as_form=as_form)


# ---------- 写测试端点(写 NAS,谨慎使用)----------
@app.post("/action/mkdir")
async def action_mkdir(request: Request, parent: str = Form(...), name: str = Form(...)):
    """创建文件夹:NAS /v2/file/newdir"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    log.info("mkdir received parent=%r name=%r", parent, name)
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await _nas_post(client, "/v2/file/newdir", {
            "parent": parent,
            "name": name,
            "rename": 0,
        })
    log.info("mkdir parent=%s name=%s → code=%s", parent, name, res.get("code"))
    return res


@app.post("/action/add-classification")
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
        res = await _nas_post(client, "/zvideo/classification/add", form)
    log.info("add-classification name=%s file_path=%s → code=%s",
             classification_name, file_path, res.get("code"))
    return res


@app.post("/action/link-folder")
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
        # 不能用 _nas_post(它 dict→form 时可能丢 [] 字段),直接打
        url = _append_common_query(f"{NAS_BASE}/zvideo/classification/increase")
        r = await client.post(url, data=form)
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("link-folder classification=%s file_path=%s → code=%s",
             classification_id, file_path, res.get("code"))
    return res


# ---------- /v2/file 完整 CRUD(基于 skyzhao1223/zspace-cli 反推 + 实测验证)----------
@app.post("/action/info")
async def action_info(request: Request, path: str = Form(...)):
    """文件/文件夹详情:NAS /v2/file/info"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await _nas_post(client, "/v2/file/info", {"path": path})
    return res


@app.post("/action/rename")
async def action_rename(request: Request, path: str = Form(...), newname: str = Form(...)):
    """改名:NAS /v2/file/modify。注意字段名是 newname,不是 name/rename"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        # 字段名 newname 是 NAS 要求,httpx data={"newname": ...} 直接传字符串就行
        url = _append_common_query(f"{NAS_BASE}/v2/file/modify")
        r = await client.post(url, data={"path": path, "newname": newname})
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("rename path=%s newname=%s → code=%s", path, newname, res.get("code"))
    return res


@app.post("/action/move")
async def action_move(request: Request, paths: str = Form(...), to: str = Form(...)):
    """移动:NAS /v2/file/move。字段 paths[](PHP 数组) + to"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        url = _append_common_query(f"{NAS_BASE}/v2/file/move")
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


@app.post("/action/copy")
async def action_copy(request: Request, paths: str = Form(...), to: str = Form(...)):
    """复制:NAS /v2/file/copy。同 move 的字段"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        url = _append_common_query(f"{NAS_BASE}/v2/file/copy")
        path_list = [p.strip() for p in paths.split(",") if p.strip()]
        form = {"to": to, "paths[]": path_list}
        r = await client.post(url, data=form)
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("copy paths=%s to=%s → code=%s", path_list, to, res.get("code"))
    return res


@app.post("/action/remove")
async def action_remove(request: Request, paths: str = Form(...)):
    """删除:NAS /v2/file/remove(端点名是 remove 不是 delete)"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        url = _append_common_query(f"{NAS_BASE}/v2/file/remove")
        path_list = [p.strip() for p in paths.split(",") if p.strip()]
        form = {"paths[]": path_list}
        r = await client.post(url, data=form)
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("remove paths=%s → code=%s", path_list, res.get("code"))
    return res
