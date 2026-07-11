"""ZSpace NAS MCP PoC - 本地登录代理 + 数据获取验证"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
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

# 会话签名密钥:优先从环境变量读(生产必须设);未设则每次启动随机生成并警告
# (随机生成意味着重启后所有已登录会话失效,且多 worker 部署会话不共享——
#  这是有意的"失败可见"行为,比硬编码公开常量安全)。
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if not SESSION_SECRET:
    SESSION_SECRET = os.urandom(32).hex()
    log.warning("SESSION_SECRET 未设置,已生成临时随机密钥(重启后所有会话失效,生产请务必设置 SESSION_SECRET 环境变量)")

_PUBKEY = load_pem_public_key(NAS_PUBKEY_PEM)


def encrypt_field(plain: str) -> str:
    """RSA-PKCS1v15 + base64. 服务端 Plugin_Util::transDecode 的逆操作."""
    cipher = _PUBKEY.encrypt(plain.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(cipher).decode("ascii")


def resolve_device_id() -> str:
    """优先用环境变量 NAS_DEVICE_ID,否则用代码里默认值。始终 32 字符。"""
    did = os.environ.get("NAS_DEVICE_ID", "").strip()
    return did if (len(did) == 32) else NAS_DEVICE_ID_DEFAULT


# SSH 凭据(用于性能监控读 /proc)
NAS_SSH_HOST = "192.168.0.135"
NAS_SSH_PORT = "57922"
NAS_SSH_USER = "15068832031"


def _ssh_perf_snapshot() -> Dict[str, Any]:
    """一次 SSH 抓全套性能指标(0.3 秒搞定,避免多次连 NAS)。
    读 /proc/loadavg /proc/stat /proc/meminfo /proc/uptime /proc/net/dev /sys/class/thermal + ps。
    """
    pw = os.environ.get("KEY_SSH", "")
    if not pw:
        return {"error": "KEY_SSH env not set"}
    cmd = (
        "echo '=== LOAD ==='; cat /proc/loadavg; "
        "echo '=== CPU ==='; head -1 /proc/stat; "
        "echo '=== MEMINFO ==='; grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree|Dirty)' /proc/meminfo; "
        "echo '=== UPTIME ==='; cat /proc/uptime; "
        "echo '=== NET DEV ==='; cat /proc/net/dev; "
        "echo '=== THERMAL ==='; for z in /sys/class/thermal/thermal_zone*; do echo -n \"$z=\"; cat $z/temp 2>/dev/null; echo; done; "
        "echo '=== TOP CPU ==='; ps -eo pid,pcpu,pmem,rss,comm --sort=-pcpu --no-headers | head -8; "
        "echo '=== TOP MEM ==='; ps -eo pid,pcpu,pmem,rss,comm --sort=-rss --no-headers | head -8; "
        "echo '=== END ==='"
    )
    try:
        r = subprocess.run(
            ["sshpass", "-p", pw, "ssh",
             "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new",
             "-p", NAS_SSH_PORT, f"{NAS_SSH_USER}@{NAS_SSH_HOST}", cmd],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {"error": "ssh timeout"}
    except FileNotFoundError:
        return {"error": "sshpass not installed"}
    if r.returncode != 0:
        msg = (r.stderr or "").strip().splitlines()[-1] if r.stderr else f"rc={r.returncode}"
        return {"error": f"ssh failed: {msg}"}
    return _parse_perf(r.stdout)


def _parse_perf(text: str) -> Dict[str, Any]:
    """解析 SSH 输出成结构化数据。"""
    out: Dict[str, Any] = {}
    sections = {}
    cur = None
    for line in text.splitlines():
        m = line.startswith("=== ") and line.endswith(" ===")
        if m:
            cur = line[4:-4]
            sections[cur] = []
        elif cur:
            sections[cur].append(line)

    if "LOAD" in sections:
        parts = (sections["LOAD"][0].split() if sections["LOAD"] else [])
        if len(parts) >= 3:
            out["loadavg"] = [float(x) for x in parts[:3]]

    if "CPU" in sections and sections["CPU"]:
        # cpu  user nice system idle iowait irq softirq steal guest guest_nice
        parts = sections["CPU"][0].split()
        if len(parts) >= 8 and parts[0] == "cpu":
            try:
                vals = [int(x) for x in parts[1:]]
            except ValueError:
                vals = None
            if vals:
                user = vals[0] if len(vals) > 0 else 0
                sys = vals[2] if len(vals) > 2 else 0
                idle = vals[3] if len(vals) > 3 else 0
                iowait = vals[4] if len(vals) > 4 else 0
                total = sum(vals)
                out["cpu_ticks"] = {"user": user, "system": sys, "idle": idle,
                                    "iowait": iowait, "total": total}
                # 自启动以来的 CPU 占用率(平均)
                if total > 0:
                    out["cpu_busy_pct_since_boot"] = round((total - idle) * 100 / total, 1)

    if "MEMINFO" in sections:
        mem = {}
        for line in sections["MEMINFO"]:
            if ":" in line:
                k, v = line.split(":", 1)
                v = v.strip().split()[0] if v.strip() else "0"
                try: mem[k] = int(v)
                except: pass
        if "MemTotal" in mem:
            total = mem["MemTotal"]
            avail = mem.get("MemAvailable", mem.get("MemFree", 0))
            cached = mem.get("Cached", 0)
            buffers = mem.get("Buffers", 0)
            swap_total = mem.get("SwapTotal", 0)
            swap_free = mem.get("SwapFree", 0)
            out["memory_kb"] = {
                "total": total, "available": avail,
                "used": total - avail, "cached": cached, "buffers": buffers,
                "swap_total": swap_total, "swap_used": swap_total - swap_free,
            }

    if "UPTIME" in sections and sections["UPTIME"]:
        parts = sections["UPTIME"][0].split()
        if len(parts) >= 2:
            out["uptime_sec"] = float(parts[0])
            out["idle_sec"] = float(parts[1])

    if "NET DEV" in sections:
        # 跳过前 2 行(表头)
        net = {}
        for line in sections["NET DEV"][2:]:
            if ":" not in line: continue
            name, rest = line.split(":", 1)
            name = name.strip()
            cols = rest.split()
            # 至少要有 16 列(RX 8 + TX 8),否则跳过该接口
            if len(cols) >= 16:
                # 关注主要接口:eth* / kvmbr* / docker0 / br-*
                if name.startswith(("eth", "kvmbr", "docker", "br-")):
                    try:
                        net[name] = {
                            "rx_bytes": int(cols[0]), "rx_packets": int(cols[1]),
                            "rx_errs": int(cols[2]), "rx_drop": int(cols[3]),
                            "tx_bytes": int(cols[8]), "tx_packets": int(cols[9]),
                            "tx_errs": int(cols[10]), "tx_drop": int(cols[11]),
                        }
                    except ValueError:
                        continue
        out["network"] = net

    if "THERMAL" in sections:
        temps = []
        for line in sections["THERMAL"]:
            if "=" in line:
                name, val = line.split("=", 1)
                try:
                    temps.append({
                        "zone": name.split("/")[-1],
                        "temp_c": round(int(val.strip()) / 1000, 1)
                    })
                except: pass
        out["temperatures"] = temps

    for kind in ("TOP CPU", "TOP MEM"):
        if kind in sections:
            procs = []
            for line in sections[kind]:
                parts = line.split()
                if len(parts) >= 5:
                    # 内核线程的 rss 可能是 "-",跳过非数字行
                    try:
                        procs.append({
                            "pid": int(parts[0]),
                            "cpu_pct": float(parts[1]),
                            "mem_pct": float(parts[2]),
                            "rss_kb": int(parts[3]),
                            "name": parts[4],
                        })
                    except ValueError:
                        continue
            key = "top_cpu_procs" if kind == "TOP CPU" else "top_mem_procs"
            out[key] = procs

    out["ts"] = int(time.time())
    return out


# 简单的内存缓存(5 秒),避免 dashboard 频繁刷新打爆 NAS
_perf_cache: Dict[str, Any] = {"ts": 0, "data": None}
_PERF_TTL = 5


def _cocoa_html_to_clean(html: str) -> str:
    """iOS Shortcut 「用多信息文本制作 HTML」产出的 Cocoa HTML Writer 风格 HTML
    → 极空间记事本能渲染的干净 HTML。

    策略:**解析** + **渲染**(不用正则硬转),这样遇到 iOS 任何奇怪变体都能处理。
    解析出结构化 block 列表:每个 block 是 (type, content) 或 (type, extra_data)。
    然后按 type 渲染成极空间能识别的 HTML。

    支持的 block type:
    - h1 / h2 / h3 / p:从 span class (s1/s2/s3/s4) 决定 heading level
    - blank:空段(只含 <br> 或空 span)
    - table:2D cell 列表
    - ul / ol:列表项
    - blockquote:引用
    """
    import re
    from html.parser import HTMLParser

    class _CocoaParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.blocks = []
            self._stack = []
            self._span_class = None

        def _top(self):
            return self._stack[-1] if self._stack else None

        @staticmethod
        def _spans_to_text_block(spans):
            """spans 列表 → ('text', btype, text) 或 None(空白)。"""
            text = "".join(s[0] for s in spans)
            if not text.strip():
                return None
            cls_nums = []
            for s, c in spans:
                if c:
                    m = re.match(r"s(\d)", c)
                    if m: cls_nums.append(int(m.group(1)))
            if cls_nums:
                btype = {1: "h1", 2: "h2", 3: "h3"}.get(min(cls_nums), "p")
            else:
                btype = "p"
            return ("text", btype, text)

        @staticmethod
        def _spans_to_plain(spans):
            return re.sub(r"\s+", " ", "".join(s[0] for s in spans)).strip()

        def handle_starttag(self, tag, attrs):
            if tag == "span":
                self._span_class = dict(attrs).get("class", "")
            elif tag == "p":
                self._stack.append({"kind": "p", "spans": []})
            elif tag == "br":
                top = self._top()
                if top and top["kind"] == "p":
                    top["spans"].append(("\n", None))
            elif tag == "table":
                self._stack.append({"kind": "table", "rows": []})
            elif tag == "tr":
                self._stack.append({"kind": "tr", "cells": []})
            elif tag in ("td", "th"):
                self._stack.append({"kind": "cell", "spans": []})
            elif tag in ("ul", "ol"):
                self._stack.append({"kind": "list", "tag": tag, "items": []})
            elif tag == "li":
                self._stack.append({"kind": "li", "spans": []})
            elif tag == "blockquote":
                self._stack.append({"kind": "blockquote", "spans": []})
            # 忽略:html/head/style/meta/title/body/tbody/thead

        def handle_endtag(self, tag):
            if tag == "span":
                self._span_class = None
            elif tag == "p":
                item = self._stack.pop()
                assert item["kind"] == "p"
                parent = self._top()
                text = "".join(s[0] for s in item["spans"])
                if not text.strip():
                    self.blocks.append(("blank",))
                    return
                if parent is None:
                    blk = self._spans_to_text_block(item["spans"])
                    if blk: self.blocks.append(blk)
                elif parent["kind"] == "cell":
                    parent["spans"].append((text, "p"))
                elif parent["kind"] == "li":
                    parent["spans"].append((text, "p"))
                elif parent["kind"] == "blockquote":
                    parent["spans"].append((text, "p"))
            elif tag == "table":
                tbl = self._stack.pop()
                rows_data = []
                for r in tbl["rows"]:
                    if r["kind"] == "tr":
                        rows_data.append([self._spans_to_plain(c["spans"]) for c in r["cells"]])
                self.blocks.append(("table", rows_data))
            elif tag == "tr":
                tr = self._stack.pop()
                parent = self._top()
                if parent and parent["kind"] == "table":
                    parent["rows"].append(tr)
            elif tag in ("td", "th"):
                cell = self._stack.pop()
                parent = self._top()
                if parent and parent["kind"] == "tr":
                    parent["cells"].append(cell)
            elif tag in ("ul", "ol"):
                lst = self._stack.pop()
                self.blocks.append(("list", lst["tag"], lst["items"]))
            elif tag == "li":
                li = self._stack.pop()
                parent = self._top()
                if parent and parent["kind"] == "list":
                    parent["items"].append(self._spans_to_plain(li["spans"]))
            elif tag == "blockquote":
                bq = self._stack.pop()
                text = self._spans_to_plain(bq["spans"])
                self.blocks.append(("blockquote", text))

        def handle_data(self, data):
            target = self._top()
            if target and target["kind"] in ("p", "cell", "li", "blockquote"):
                target["spans"].append((data, self._span_class))

        def error(self, msg):
            pass

    def _render(blocks):
        out = []
        for b in blocks:
            if b[0] == "blank":
                out.append("<p>&nbsp;</p>")
            elif b[0] == "text":
                _, btype, text = b
                out.append(f"<{btype}>{text}</{btype}>")
            elif b[0] == "table":
                _, rows = b
                rhtml = ""
                for row in rows:
                    cells = "".join(f"<td>{c}</td>" for c in row)
                    rhtml += f"<tr>{cells}</tr>"
                out.append(f'<table border="1">{rhtml}</table>')
            elif b[0] == "list":
                _, tag, items = b
                lhtml = "".join(f"<li>{i}</li>" for i in items)
                out.append(f"<{tag}>{lhtml}</{tag}>")
            elif b[0] == "blockquote":
                _, text = b
                out.append(f"<blockquote>{text}</blockquote>")
        return "\n".join(out)

    parser = _CocoaParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return ""  # 解析失败兜底,服务端会再走"纯文本"路径
    return _render(parser.blocks).strip()


def _get_perf_cached() -> Dict[str, Any]:
    now = time.time()
    if _perf_cache["data"] and now - _perf_cache["ts"] < _PERF_TTL:
        return _perf_cache["data"]
    data = _ssh_perf_snapshot()
    # 只缓存成功结果;失败的(含 error 键)每次重试,避免 5 秒内一直显示旧错误
    if "error" not in data:
        _perf_cache["data"] = data
        _perf_cache["ts"] = now
    return data


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
async def dashboard_root(request: Request):
    """旧入口重定向到 overview tab。"""
    return RedirectResponse("/dashboard/overview", status_code=303)


def _require_login(request: Request):
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return None, RedirectResponse("/login", status_code=303)
    return cookies, None


def _common_ctx(request: Request, cookies) -> dict:
    return {
        "user": request.session.get("nas_user", {}),
        "cookies_keys": list(cookies.keys()),
    }


@app.get("/dashboard/overview", response_class=HTMLResponse)
async def tab_overview(request: Request):
    """总览 tab:监控(zstatus)+ 性能快照(SSH /proc)。"""
    cookies, redirect = _require_login(request)
    if redirect: return redirect

    import asyncio
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        async def _g(coro):
            async with sem:
                return await coro
        monitor_html, = await asyncio.gather(
            _g(client.get(_append_common_query(f"{NAS_BASE}/zstatus"))),
        )

    perf = _get_perf_cached()
    return templates.TemplateResponse(
        request,
        "tab_overview.html",
        {
            **_common_ctx(request, cookies),
            "active_tab": "overview",
            "monitor": parse_zstatus(monitor_html.text),
            "perf": perf,
        },
    )


@app.get("/dashboard/storage", response_class=HTMLResponse)
async def tab_storage(request: Request):
    """存储 tab:存储池 + 文件夹浏览 + 文件写测试。"""
    cookies, redirect = _require_login(request)
    if redirect: return redirect

    import asyncio
    file_path = request.query_params.get("path") or "/sata14/my/data/"
    if not file_path.startswith("/"):
        file_path = "/" + file_path
    if not file_path.endswith("/"):
        file_path = file_path + "/"
    breadcrumb = build_breadcrumb(file_path)

    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        async def _g(coro):
            async with sem:
                return await coro
        zspool_info, zspool_hw, file_resp = await asyncio.gather(
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
        )

    # 检查 test 文件夹是否存在(写测试状态)
    test_dir_exists = False
    if file_resp.get("code") == "200":
        for it in (file_resp.get("data", {}).get("list") or []):
            if it.get("name") == "test" and it.get("is_dir") == "1":
                if it.get("path", "").endswith("/备份/test"):
                    test_dir_exists = True
                    break
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

    return templates.TemplateResponse(
        request,
        "tab_storage.html",
        {
            **_common_ctx(request, cookies),
            "active_tab": "storage",
            "zspool_info": zspool_info,
            "zspool_hw": zspool_hw,
            "file_path": file_path,
            "file_resp": file_resp,
            "breadcrumb": breadcrumb,
            "test_dir_exists": test_dir_exists,
        },
    )


@app.get("/dashboard/zvideo", response_class=HTMLResponse)
async def tab_zvideo(request: Request):
    """极影视 tab:分类列表 + 源目录 + 影视写测试。"""
    cookies, redirect = _require_login(request)
    if redirect: return redirect

    import asyncio
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        async def _g(coro):
            async with sem:
                return await coro
        zvideo_classes, zvideo_dirs = await asyncio.gather(
            _g(_nas_post(client, "/zvideo/classification/list", {})),
            _g(_nas_post(client, "/zvideo/classification/dirs", {})),
        )

    test_class_exists = False
    if zvideo_classes.get("code") == "200":
        for c in (zvideo_classes.get("data") or []):
            if c.get("name") == "test":
                test_class_exists = True
                break

    return templates.TemplateResponse(
        request,
        "tab_zvideo.html",
        {
            **_common_ctx(request, cookies),
            "active_tab": "zvideo",
            "zvideo_classes": zvideo_classes,
            "zvideo_dirs": zvideo_dirs,
            "test_class_exists": test_class_exists,
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


@app.get("/api/perf")
async def api_perf(request: Request):
    """性能监控 JSON(LAN SSH 读 /proc,5 秒缓存)。需登录。"""
    if not request.session.get("nas_cookies"):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return _get_perf_cached()


# ---------- iPhone Shortcut 同步入口(独立 NAS session,与 dashboard 登录无关)----------
_shortcut_nas_client: Optional[httpx.AsyncClient] = None
_shortcut_nas_lock = asyncio.Lock()


async def _get_shortcut_nas_client() -> Optional[httpx.AsyncClient]:
    """Service-account 风格:首次用 NAS_USER/NAS_PASSWORD env 登录 NAS,缓存 client 给后续用。
    cookies 在 NAS 超时才会失效,届时需要重启 app 重登。
    """
    global _shortcut_nas_client
    if _shortcut_nas_client is not None:
        return _shortcut_nas_client
    async with _shortcut_nas_lock:
        if _shortcut_nas_client is not None:
            return _shortcut_nas_client
        nas_user = os.environ.get("NAS_USER", "").strip()
        nas_pass = os.environ.get("NAS_PASSWORD", "").strip()
        if not nas_user or not nas_pass:
            log.error("SHORTCUT: NAS_USER/NAS_PASSWORD not set")
            return None
        device_id = resolve_device_id()
        # 跟 dashboard login_submit 完全一致:用 plaintext form-urlencoded 给 httpx,
        # 显式构造 cookies(包含 token + 全部 resp.cookies)。
        client = httpx.AsyncClient(timeout=10)
        form = {
            "username": encrypt_field(nas_user),
            "password": encrypt_field(nas_pass),
            "plat": "web",
            "device": "linux",
            "device_id": device_id,
        }
        try:
            resp = await client.post(f"{NAS_BASE}/auth/login", data=form)
        except httpx.HTTPError as e:
            log.error("SHORTCUT: NAS login HTTP error %s", e)
            await client.aclose()
            return None
        try:
            body = resp.json()
        except Exception:
            await client.aclose()
            return None
        if str(body.get("code")) != "200":
            log.error("SHORTCUT: NAS login rejected %s", body)
            await client.aclose()
            return None
        # 显式组装 cookies(dashboard /login 里就是这么干的,不开这个会 403)
        data = body.get("data") or {}
        explicit_cookies: Dict[str, str] = {
            "token": data.get("token", ""),
            "username": nas_user,
            "device_id": device_id,
            "device": "linux",
            "plat": "web",
        }
        for ck, cv in resp.cookies.items():
            explicit_cookies[ck] = cv
        # 重建 client with explicit cookies,丢掉 client 内置的 jar
        await client.aclose()
        client = httpx.AsyncClient(timeout=10, cookies=explicit_cookies)
        _shortcut_nas_client = client
        log.info("SHORTCUT: NAS login ok, session cached (token=%.8s...)", explicit_cookies["token"])
        return client


async def _reset_shortcut_nas_client() -> None:
    """丢弃缓存的 shortcut client(用于 token 失效后强制下次重登)。

    取代旧的"只能重启 app"恢复方式。"""
    global _shortcut_nas_client
    async with _shortcut_nas_lock:
        old = _shortcut_nas_client
        _shortcut_nas_client = None
    if old is not None:
        try:
            await old.aclose()
        except Exception:
            pass
        log.info("SHORTCUT: cached client reset (will re-login on next request)")


def _title_eq(a: str, b: str) -> bool:
    """同名查重的"等价"判断:emoji 在 NAS 里两种形态都可能出现
    (UTF-8 字符 🐶 vs entity &#128054;),但语义上是同一条。
    两边都规整到 entity 形式再比,避免重复备份。
    """
    if not a or not b:
        return a == b
    if a == b:
        return True
    import re as _re_eq
    def _to_entity(s: str) -> str:
        return _re_eq.sub(r"[^\x00-\x7f]", lambda m: f"&#{ord(m.group(0))};", s)
    try:
        return _to_entity(a) == _to_entity(b)
    except Exception:
        return False


@app.post("/shortcut/notepad")
async def shortcut_notepad(request: Request):
    """iPhone Shortcut 同步入口(单向 iPhone 备忘录 → NAS 记事本)。

    Headers:
      X-Shortcut-Key: <env SHORTCUT_KEY>  ← 静态预共享密钥,防 LAN 上任意写入

    Body (JSON):
      title (str,必填)
      body  (str,HTML 或纯文本,自动加 <h1>{title}</h1> 前缀)
      classify_id (int,可选,默认 0 = 未分类,必须是叶子分类 id)

    行为:
      - title 已存在(精确匹配已存在笔记标题) → 200, exists=true, 不覆盖
      - 否则 → 调 NAS /v2/file/notepad/new,返回 id

    请求体两种格式都支持:
      - text/plain 或没声明 Content-Type:整段文本就是笔记内容,服务端从第一行抽 title
      - application/json:{"body": "...", "title": "...(可选)", "classify_id": N(可选)}

    安全模式(默认):
      .env 里设 SHORTCUT_KEY=<随机密钥> → 必须带 X-Shortcut-Key 头
      .env 里 SHORTCUT_KEY 留空 → 默认拒绝;设 ALLOW_OPEN_SHORTCUT=1 才开放(仅信任 LAN)

    文档:
      docs/iphone-shortcut.md(iPhone 快捷指令配置步骤)
    """
    # 鉴权:env 设了 SHORTCUT_KEY 必须带正确密钥;留空时默认拒绝,
    # 除非显式设 ALLOW_OPEN_SHORTCUT=1(开放模式,仅信任 LAN)。
    expected = os.environ.get("SHORTCUT_KEY", "").strip()
    got = request.headers.get("X-Shortcut-Key", "").strip()
    if expected:
        if got != expected:
            log.warning("SHORTCUT: invalid/missing key from %s", request.client.host if request.client else "?")
            return JSONResponse({"error": "invalid X-Shortcut-Key"}, status_code=401)
    elif os.environ.get("ALLOW_OPEN_SHORTCUT", "").strip() not in ("1", "true", "yes"):
        log.warning("SHORTCUT: rejected open-mode request (set SHORTCUT_KEY or ALLOW_OPEN_SHORTCUT=1) from %s",
                    request.client.host if request.client else "?")
        return JSONResponse(
            {"error": "shortcut endpoint requires SHORTCUT_KEY or ALLOW_OPEN_SHORTCUT=1"},
            status_code=403,
        )

    # body 解析:JSON / text/plain / 无 Content-Type 都支持
    content_type = request.headers.get("Content-Type", "").lower()
    body = ""
    classify_id = 0
    title = ""
    # 调试:把每次请求的原始内容记到文件(默认关,设 SHORTCUT_DEBUG=1 开启)
    if os.environ.get("SHORTCUT_DEBUG"):
        try:
            _raw_dbg = await request.body()
            _decoded_dbg = _raw_dbg.decode("utf-8", errors="replace")
            # form-urlencoded 形式(=XXXXX),剥前缀 = 解码 URL → 原始 body
            if content_type.startswith("application/x-www-form-urlencoded"):
                from urllib.parse import unquote_plus, parse_qs
                _parsed = parse_qs(_decoded_dbg, keep_blank_values=True)
                # 找第一个非空 value 当作真实 body
                _body_dbg = ""
                for k in ("body", "text", ""):
                    if _parsed.get(k) and _parsed[k][0]:
                        _body_dbg = _parsed[k][0]
                        break
                if not _body_dbg:
                    _body_dbg = unquote_plus(_decoded_dbg.lstrip("="))
            else:
                _body_dbg = _decoded_dbg
            with open("/tmp/shortcut_debug.log", "a") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
                f.write(f"client: {request.client.host if request.client else '?'}\n")
                f.write(f"Content-Type: {content_type!r}\n")
                f.write(f"raw body: {len(_raw_dbg)} bytes, decoded body: {len(_body_dbg)} chars\n")
                f.write(f"--- DECODED body (full) ---\n")
                f.write(_body_dbg)
                f.write("\n--- END ---\n")
        except Exception as _e:
            with open("/tmp/shortcut_debug.log", "a") as f:
                f.write(f"debug log failed: {_e}\n")
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        body = payload.get("body", "") or payload.get("text", "") or ""
        title = (payload.get("title") or "").strip()
        classify_id = int(payload.get("classify_id") or 0)
    elif "application/x-www-form-urlencoded" in content_type or content_type == "application/x-www-form-urlencoded":
        # iOS Shortcut 「获取 URL 内容」+ 请求正文=文本 实际发的是 form-urlencoded
        # 格式通常是 =<整段文本>(空 key 的单字段)
        raw = await request.body()
        body_text = raw.decode("utf-8", errors="replace")
        from urllib.parse import parse_qs
        parsed = parse_qs(body_text, keep_blank_values=True)
        # 优先 body/text 命名字段;否则取第一个 value;最后兜底用整段解码文本
        body = ""
        for k in ("body", "text", ""):
            if parsed.get(k):
                body = parsed[k][0]
                break
        if not body:
            body = body_text
        title = (request.query_params.get("title") or "").strip()
        try:
            classify_id = int(request.query_params.get("classify_id") or 0)
        except ValueError:
            classify_id = 0
    else:
        # text/plain 或其他:整段就是笔记内容
        raw = await request.body()
        body = raw.decode("utf-8", errors="replace")
        # 可选 query 参数 ?title=...&classify_id=N
        title = (request.query_params.get("title") or "").strip()
        try:
            classify_id = int(request.query_params.get("classify_id") or 0)
        except ValueError:
            classify_id = 0

    # ----- Cocoa HTML 检测 + 转干净 HTML -----
    # iOS Shortcut 「用多信息文本制作 HTML」输出 Cocoa HTML Writer 风格的完整 HTML 文档,
    # 极空间记事本只认简单标签(不认 .AppleSystemUIFont / class="p1/s1"/ inline CSS),
    # 原样存会导致 emoji 字体缺失、表格无边框、标题样式失效。
    # 检测到特征就转成 <h1>/<h2>/<h3>/<p>/<table border="1"> 的干净 HTML。
    if "Cocoa HTML Writer" in body or ".AppleSystemUIFont" in body:
        body = _cocoa_html_to_clean(body)

    # ----- UTF-8 emoji → 数字 HTML entity(确保 app 详情能渲染)-----
    # ZSpace app 列表摘要(in_brief)能正确显示 UTF-8 emoji,但详情 body 渲染对
    # UTF-8 emoji 字体回退失败导致不显示;反之 app 详情能正确渲染 &#数字; 形式的 entity。
    # 服务端落 NAS 前只把 emoji 范围字符转成 &#数字; entity,中文/标点不动。
    # emoji Unicode 范围: 主要在 U+1F300-U+1FAFF(补充符号 + 表情)+ U+2600-U+27BF(杂项符号)
    # + U+1F000-U+1F1FF(麻将/扑克等)+ U+1F900-U+1F9FF(补充符号和象形文字)
    import re as _re_ent
    def _encode_entity(m: "_re_ent.Match[str]") -> str:
        return f"&#{ord(m.group(0))};"
    _emoji_pattern = (
        r"[\U0001F000-\U0001F02F"          # 麻将牌
        r"\U0001F0A0-\U0001F0FF"          # 扑克牌
        r"\U0001F100-\U0001F1FF"          # 封闭字母数字补充
        r"\U0001F200-\U0001F2FF"          # 封闭表意文字补充
        r"\U0001F300-\U0001F5FF"          # 符号和象形文字
        r"\U0001F600-\U0001F64F"          # 表情
        r"\U0001F680-\U0001F6FF"          # 交通和地图
        r"\U0001F700-\U0001F77F"          # 炼金术
        r"\U0001F780-\U0001F7FF"          # 几何形状扩展
        r"\U0001F800-\U0001F8FF"          # 补充箭头-C
        r"\U0001F900-\U0001F9FF"          # 补充符号和象形文字
        r"\U0001FA00-\U0001FA6F"          # 棋盘符号
        r"\U0001FA70-\U0001FAFF]"         # 符号和象形文字扩展-A
    )
    body = _re_ent.sub(_emoji_pattern, _encode_entity, body)

    # title 为空时,优先从 HTML body 的 <h1> 抽;否则从第一非空行抽(纯文本路径)。
    # 同时把抽出来的"标题源"从 body 里去掉,避免 <h1> 标题 + body 首行重复。
    if not title:
        import re as _re_title
        stripped_body = body.strip()
        if stripped_body.startswith("<"):
            # 富文本路径:iOS Shortcut 「用多信息文本制作 HTML」送来的 HTML
            m = _re_title.search(
                r"<h1[^>]*>(.*?)</h1>", stripped_body, _re_title.DOTALL | _re_title.IGNORECASE
            )
            if m:
                # 抽 <h1> 内部纯文本(去嵌套标签)做 title
                inner = _re_title.sub(r"<[^>]+>", "", m.group(1))
                title = inner.strip()[:200]
                # 把这个 <h1> 整段从 body 里删掉(避免和服务端自动加的 h1 重复)
                body = (stripped_body[:m.start()] + stripped_body[m.end():]).strip()
            else:
                # 没 <h1>:取全部可见文本前 200 字符
                text = _re_title.sub(r"<[^>]+>", " ", stripped_body)
                text = _re_title.sub(r"\s+", " ", text).strip()
                if text:
                    title = text[:200]
        else:
            # 纯文本路径(原有逻辑)
            first_line = ""
            rest_lines = []
            found = False
            for line in body.splitlines():
                stripped = line.strip()
                if not found and stripped:
                    first_line = stripped[:200]
                    found = True
                    continue  # 跳过第一行,不进 rest_lines
                rest_lines.append(line)
            if first_line:
                title = first_line
                body = "\n".join(rest_lines).lstrip("\n")
        if not title:
            return JSONResponse({"error": "title required (and body has no first line to derive from)"}, status_code=400)
    if len(title) > 200:
        return JSONResponse({"error": "title too long (max 200 chars)"}, status_code=400)
    if len(body) > 500_000:
        return JSONResponse({"error": "body too long (max 500KB)"}, status_code=413)

    client = await _get_shortcut_nas_client()
    if client is None:
        return JSONResponse(
            {"error": "NAS login failed (check NAS_USER/NAS_PASSWORD env on host)"},
            status_code=502,
        )

    # 1) 同名查重(精确匹配 title)
    # NAS /v2/file/notepad/searchnotepad 返回 data.list(嵌套 dict),title 会被裹 "..." 标记
    search_resp = await _nas_post(client, "/v2/file/notepad/searchnotepad", {
        "keyword": title, "num": 10, "location": 2,
    })
    # token 失效(N001208):重置缓存 client 重登一次,再重试 search
    if str(search_resp.get("code")) == "N001208":
        await _reset_shortcut_nas_client()
        client = await _get_shortcut_nas_client()
        if client is not None:
            search_resp = await _nas_post(client, "/v2/file/notepad/searchnotepad", {
                "keyword": title, "num": 10, "location": 2,
            })
    if str(search_resp.get("code")) == "200":
        data = search_resp.get("data") or {}
        notes = data.get("list") if isinstance(data, dict) else data
        for note in notes or []:
            if not isinstance(note, dict):
                continue
            raw_title = (note.get("title") or "")
            # 去 NAS 标记 ...(实测可能是开头 + 收尾两个字符)
            stripped = raw_title.replace("\x01", "").replace("\x02", "").strip()
            if _title_eq(stripped, title):
                return JSONResponse({
                    "ok": True,
                    "exists": True,
                    "id": note.get("id"),
                    "skipped_reason": "title already exists (overwrite disabled by user policy)",
                })

    # 2) 自动加 h1 前缀(防 body 对但 NAS 存空的坑)
    # 已含 <h1>(title 完全一致 OR iOS 富文本路径送来的 HTML,容忍带属性)就不重复加
    body_starts = body.lstrip()
    low = body_starts[:4].lower()
    after = body_starts[4:5]  # <h1 后面第 5 个字符
    # 匹配 <h1> 或 <h1 ...>(容忍 class/style 等属性)
    already_has_h1 = low == "<h1>" or (low == "<h1" and after in (" ", "\t", "\n", ">"))
    exact_h1_match = body_starts.startswith(f"<h1>{title}</h1>")
    if not already_has_h1 and not exact_h1_match:
        body = f"<h1>{title}</h1>\n{body}"

    # 3) 写入
    new_resp = await _nas_post(client, "/v2/file/notepad/new", {
        "title": title, "body": body, "classify_id": classify_id, "location": 2,
    })
    if str(new_resp.get("code")) != "200":
        return JSONResponse({
            "ok": False,
            "exists": False,
            "error": new_resp.get("msg") or "NAS rejected",
            "nas_response": new_resp,
        }, status_code=502)
    new_data = new_resp.get("data") or {}
    new_id = new_data.get("id") if isinstance(new_data, dict) else None

    # 4) "激活"渲染:ZSpace app 只在用户手动编辑保存后才正确渲染 emoji;
    # 服务端在新建后立刻用相同 body 再调一次 modify,模拟 app 保存动作,
    # 触发 NAS 后端的 emoji 渲染初始化。
    if new_id:
        try:
            await _nas_post(client, "/v2/file/notepad/modify", {
                "id": new_id, "title": title, "body": body,
                "classify_id": classify_id, "location": 2,
            })
            log.info("SHORTCUT: activated render for id=%s", new_id)
        except Exception as _e:
            log.warning("SHORTCUT: activate render failed for id=%s: %s", new_id, _e)

    return JSONResponse({
        "ok": True, "exists": False, "id": new_id,
    })


@app.get("/n", response_class=HTMLResponse)
async def notepad_pwa():
    """iPhone Safari 上的 PWA 记事本推送表单(不需要装 Shortcuts,不需要密钥)。

    用法:
      1. iPhone Safari 打开 http://192.168.0.123:8000/n
      2. 分享 → 添加到主屏幕,以后桌面图标直接进(全屏,跟 app 一样)
      3. 填 title + body,点推送(开放模式,服务端自己抽 title 去重)
    """
    return HTMLResponse(_PWA_NOTEPAD_HTML)


_PWA_NOTEPAD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="推 NAS 笔记">
<title>推 NAS 笔记</title>
<style>
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", "PingFang SC", sans-serif;
    background: #f2f2f7; color: #000;
    -webkit-text-size-adjust: 100%;
  }
  .wrap { max-width: 600px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 22px; margin: 4px 0 16px; }
  .card {
    background: #fff; border-radius: 12px;
    padding: 14px 16px; margin-bottom: 12px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  }
  label { display: block; font-size: 13px; color: #6c6c70; margin-bottom: 6px; }
  input[type=text], textarea {
    width: 100%; padding: 10px 12px;
    font-size: 16px; font-family: inherit;
    border: 1px solid #d1d1d6; border-radius: 8px;
    background: #fff; color: #000; outline: none;
  }
  input[type=text]:focus, textarea:focus { border-color: #007aff; }
  textarea { min-height: 140px; resize: vertical; }
  .row { display: flex; gap: 8px; margin-top: 8px; }
  button {
    flex: 1; padding: 12px 16px;
    font-size: 16px; font-weight: 600;
    border: none; border-radius: 10px;
    background: #007aff; color: #fff;
    -webkit-tap-highlight-color: transparent;
  }
  button:active { background: #0062cc; }
  button.secondary { background: #e5e5ea; color: #007aff; }
  button.secondary:active { background: #d1d1d6; }
  #status {
    margin-top: 12px; padding: 12px 14px;
    border-radius: 10px; font-size: 14px;
    display: none; word-break: break-all;
  }
  #status.ok    { background: #d4f7dc; color: #1f6f2b; display: block; }
  #status.skip  { background: #fff3cd; color: #7a5d00; display: block; }
  #status.err   { background: #ffd6d6; color: #8b0000; display: block; }
  .hint { font-size: 12px; color: #8e8e93; margin-top: 6px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>推送到 NAS 记事本</h1>

  <div class="card">
    <label>标题 title</label>
    <input id="title" type="text" placeholder="例如:周报 2026-07-01" autocomplete="off">
  </div>

  <div class="card">
    <label>正文 body(自动加 &lt;h1&gt;title&lt;/h1&gt; 前缀)</label>
    <textarea id="body" placeholder="随便写,支持 HTML"></textarea>
  </div>

  <div class="row">
    <button onclick="submitNote()">推送</button>
  </div>

  <div id="status"></div>

  <div class="hint" style="margin-top:24px">
    同名标题会自动跳过(不覆盖)。把页面加到主屏幕,从桌面图标打开更顺手。
  </div>
</div>

<script>
  function setStatus(kind, msg) {
    const el = document.getElementById("status");
    el.className = kind;
    el.textContent = msg;
  }

  async function submitNote() {
    const title = document.getElementById("title").value.trim();
    const body  = document.getElementById("body").value;
    if (!title) { setStatus("err", "标题必填"); return; }

    setStatus("ok", "推送中…");
    try {
      const resp = await fetch("/shortcut/notepad", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, body }),
      });
      const text = await resp.text();
      let j = {};
      try { j = JSON.parse(text); } catch (_) {}
      if (resp.status === 200 && j.ok && !j.exists) {
        setStatus("ok", "✅ 已推送 id=" + j.id);
      } else if (resp.status === 200 && j.exists) {
        setStatus("skip", "⏭️ 跳过(同名已存在) id=" + j.id);
      } else if (resp.status === 413) {
        setStatus("err", "❌ body 超 500KB");
      } else {
        setStatus("err", "❌ " + (j.error || resp.status) + (j.nas_response ? " " + JSON.stringify(j.nas_response) : ""));
      }
    } catch (e) {
      setStatus("err", "❌ 网络错误 " + e);
    }
  }

  // 启动时:title 框粘贴事件,自动从剪贴板抓首行
  document.getElementById("title").addEventListener("paste", (e) => {
    setTimeout(() => {
      const t = document.getElementById("title");
      if (t.value.trim()) return;
      const pasted = (e.clipboardData || window.clipboardData).getData("text");
      const firstLine = (pasted || "").split(/\\r?\\n/)[0].slice(0, 80);
      if (firstLine) t.value = firstLine;
    }, 0);
  });
</script>
</body>
</html>
"""


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


# ---------- 独立记事本 (location=2) ----------
import bleach


def _safe_html(html: str) -> str:
    """清理笔记 content 字段(可能含富文本),白名单标签 + 属性 + 协议,strip 危险内容。"""
    if not html:
        return ""
    return bleach.clean(
        html,
        tags=[
            "p", "br", "b", "strong", "i", "em", "u", "s", "del",
            "ul", "ol", "li",
            "h1", "h2", "h3", "h4", "h5", "h6",
            "blockquote", "code", "pre",
            "img", "a", "span", "div", "hr",
            "table", "thead", "tbody", "tr", "th", "td",
        ],
        attributes={
            "a": ["href", "title", "target", "rel"],
            "img": ["src", "alt", "width", "height", "title"],
            "span": ["style"],
            "div": ["style"],
            "p": ["style"],
        },
        protocols=["http", "https", "data", "mailto"],
        strip=True,
    )


templates.env.filters["safe_html"] = _safe_html


@app.get("/dashboard/notebook", response_class=HTMLResponse)
async def tab_notebook(request: Request):
    """记事本 tab:总览 metric + 分类侧栏 + 笔记列表 + 写测试区。

    默认取:
    - totalsize (总占用)
    - allclassify (含嵌套的分类树,给侧栏用)
    - list?classify_id=0 (全部笔记,默认视图)
    """
    cookies, redirect = _require_login(request)
    if redirect:
        return redirect

    import asyncio
    sem = asyncio.Semaphore(1)  # 关键:串行,保 N150
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        async def _g(coro):
            async with sem:
                return await coro
        totalsize, allclassify, trashcount = await asyncio.gather(
            _g(_nas_post(client, "/v2/file/notepad/totalsize", {"location": 2})),
            _g(_nas_post(client, "/v2/file/notepad/allclassify", {"location": 2})),
            _g(_nas_post(client, "/v2/file/notepad/list", {
                "classify_id": -1, "start": 0, "num": 1, "location": 2,
            })),
        )
        notelist = await _nas_post(client, "/v2/file/notepad/list", {
            "classify_id": 0, "start": 0, "num": 50, "location": 2,
        })

    classify_tree = ((allclassify.get("data") or {}).get("list") or []) if str(allclassify.get("code")) == "200" else []
    trash_n = ((trashcount.get("data") or {}).get("total") or 0) if str(trashcount.get("code")) == "200" else 0

    return templates.TemplateResponse(
        request, "tab_notebook.html",
        {
            **_common_ctx(request, cookies),
            "active_tab": "notebook",
            "totalsize": totalsize,
            "allclassify_resp": allclassify,
            "classify_tree": classify_tree,
            "trash_count": trash_n,
            "notelist": notelist,
            "current_classify_id": 0,
        },
    )


# ---- 读 action(GET,前端 fetch 用)----
# notebook-list 在 §6.3.2 "完整化" 区里有带 start 参数的版本(L1159),这里不重复定义


@app.get("/action/notebook-info")
async def action_notebook_info(request: Request, id: int):
    """笔记详情(content 字段在 data.content,可能含富文本)。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, "/v2/file/notepad/info", {
            "id": id, "location": 2,
        })


@app.get("/action/notebook-search")
async def action_notebook_search(request: Request, keyword: str, num: int = 50):
    """搜索笔记。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, "/v2/file/notepad/searchnotepad", {
            "keyword": keyword, "num": num, "location": 2,
        })


@app.get("/action/notebook-history")
async def action_notebook_history(request: Request, id: int):
    """历史版本列表。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, "/v2/file/notepad/historylist", {
            "id": id, "location": 2,
        })


@app.get("/action/notebook-getconfig")
async def action_notebook_getconfig(request: Request):
    """读配置。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, "/v2/file/notepad/getconfig", {"location": 2})


@app.get("/action/notebook-classifylist")
async def action_notebook_classifylist(request: Request, start: int = 0, num: int = 50):
    """分类列表(供前端刷新侧栏)。默认只列顶层(parent_id=0);带 parent_id=N 查直接子。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, "/v2/file/notepad/classifylist", {
            "start": start, "num": num, "location": 2,
        })


@app.get("/action/notebook-allclassify")
async def action_notebook_allclassify(request: Request):
    """完整分类树(含嵌套)。每个节点字段:
    {id, name, parent_id, child: [...]}。
    客户端要做"分类1 下"聚合时:遍历该树,递归对每个叶子调 list?classify_id=leaf.id。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, "/v2/file/notepad/allclassify", {
            "location": 2,
        })


@app.get("/action/notebook-totalsize")
async def action_notebook_totalsize(request: Request):
    """总占用(供前端刷新 metric)"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, "/v2/file/notepad/totalsize", {"location": 2})


# ---- 写 action(POST,字段名全部已确认)----
@app.post("/action/notebook-new")
async def action_notebook_new(request: Request,
                                title: str = Form(...),
                                body: str = Form(""),
                                classify_id: int = Form(0)):
    """⚠️ 关键:
    - body 字段(不是 content!)+ in_brief + classify_id + location
    - body 必须以 `<h1>{title}</h1>` 开头,否则 NAS 不存内容(实测)
    - in_brief 从 body 去 HTML 后截前 100 字符
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    import re as _re
    # 自动加 h1 标题前缀(参考 pcweb HAR 抓包:body 必以 <h1>{title}</h1> 开头)
    body = body or ""
    h1_prefix = f"<h1>{title}</h1>"
    if not body.lstrip().startswith("<h1>"):
        body = h1_prefix + body
    plain = _re.sub(r"<[^>]+>", " ", body).strip()
    in_brief = plain[:100]
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await _nas_post(client, "/v2/file/notepad/new", {
            "title": title, "body": body, "in_brief": in_brief,
            "classify_id": classify_id, "location": 2,
        })
    log.info("notebook-new title=%r body=%d chars → code=%s",
             title[:40], len(body), res.get("code"))
    return res


@app.post("/action/notebook-modify")
async def action_notebook_modify(request: Request,
                                   id: int = Form(...),
                                   title: str = Form(...),
                                   body: str = Form("")):
    """⚠️ body 必以 `<h1>{title}</h1>` 开头,否则 NAS 不存内容(实测)。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    import re as _re
    body = body or ""
    h1_prefix = f"<h1>{title}</h1>"
    if not body.lstrip().startswith("<h1>"):
        body = h1_prefix + body
    plain = _re.sub(r"<[^>]+>", " ", body).strip()
    in_brief = plain[:100]
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await _nas_post(client, "/v2/file/notepad/modify", {
            "id": id, "title": title, "body": body, "in_brief": in_brief, "location": 2,
        })
    log.info("notebook-modify id=%s title=%r body=%d chars → code=%s",
             id, title[:40], len(body), res.get("code"))
    return res


@app.post("/action/notebook-delete")
async def action_notebook_delete(request: Request, id: int = Form(...)):
    """单删笔记:NAS /v2/file/notepad/delete,字段是 `ids[]`(pcweb HAR 抓包确认)。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        url = _append_common_query(f"{NAS_BASE}/v2/file/notepad/delete")
        # NAS 字段名是 `ids[]`(PHP 数组),即使单删也是
        r = await client.post(url, data={"ids[]": [id], "location": "2"})
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("notebook-delete id=%s → code=%s", id, res.get("code"))
    return res


@app.post("/action/notebook-delete-batch")
async def action_notebook_delete_batch(request: Request, ids: str = Form(...)):
    """批量删除:NAS /v2/file/notepad/delete 接 PHP 数组 `ids[]`(pcweb HAR 抓包确认)。
    ids 用逗号分隔(如 "16,17,18"),服务端会展开成 ids[]=16&ids[]=17&ids[]=18。
    NAS 端行为未验证:本端点**只做透传**,原样返回 NAS response。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    if not id_list:
        return {"error": "no valid ids"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        url = _append_common_query(f"{NAS_BASE}/v2/file/notepad/delete")
        # 关键字段名:`ids[]`(pcweb HAR 抓包,带 s)
        r = await client.post(url, data={"ids[]": id_list, "location": "2"})
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("notebook-delete-batch ids=%s → code=%s", id_list, res.get("code"))
    return res


@app.post("/action/notebook-pin")
async def action_notebook_pin(request: Request, id: int = Form(...), is_top: int = Form(1)):
    """is_top: 1=置顶, 0=取消置顶。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await _nas_post(client, "/v2/file/notepad/pin", {
            "id": id, "is_top": is_top, "location": 2,
        })
    log.info("notebook-pin id=%s is_top=%s → code=%s", id, is_top, res.get("code"))
    return res


@app.post("/action/notebook-updatelabel")
async def action_notebook_updatelabel(request: Request,
                                        id: int = Form(...),
                                        label: str = Form("")):
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await _nas_post(client, "/v2/file/notepad/updatelabel", {
            "id": id, "label": label, "location": 2,
        })
    log.info("notebook-updatelabel id=%s label=%r → code=%s", id, label, res.get("code"))
    return res


@app.post("/action/notebook-movenotepad")
async def action_notebook_movenotepad(request: Request,
                                       id: int = Form(...),
                                       classify_id: int = Form(...)):
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await _nas_post(client, "/v2/file/notepad/movenotepad", {
            "id": id, "classify_id": classify_id, "location": 2,
        })
    log.info("notebook-movenotepad id=%s → classify=%s → code=%s",
             id, classify_id, res.get("code"))
    return res


@app.post("/action/notebook-newclassify")
async def action_notebook_newclassify(request: Request,
                                       name: str = Form(...),
                                       parent_id: int = Form(0)):
    """parent_id=0 表示顶级分类。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await _nas_post(client, "/v2/file/notepad/newclassify", {
            "name": name, "parent_id": parent_id, "location": 2,
        })
    log.info("notebook-newclassify name=%r parent=%s → code=%s",
             name, parent_id, res.get("code"))
    return res


@app.post("/action/notebook-deleteclassify")
async def action_notebook_deleteclassify(request: Request,
                                          classify_id: int = Form(...)):
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await _nas_post(client, "/v2/file/notepad/deleteclassify", {
            "classify_id": classify_id, "location": 2,
        })
    log.info("notebook-deleteclassify classify_id=%s → code=%s",
             classify_id, res.get("code"))
    return res


@app.post("/action/notebook-updateclassify")
async def action_notebook_updateclassify(request: Request,
                                          classify_id: int = Form(...),
                                          new_name: str = Form(...)):
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        res = await _nas_post(client, "/v2/file/notepad/updateclassify", {
            "classify_id": classify_id, "new_name": new_name, "location": 2,
        })
    log.info("notebook-updateclassify classify_id=%s new_name=%r → code=%s",
             classify_id, new_name, res.get("code"))
    return res


# ============================================================================
# 完整化:MCP 用的端点,把 NAS notepad/* 全暴露
# ============================================================================

# ---- list 加 start 参数(分页) ----
@app.get("/action/notebook-list")
async def action_notebook_list(request: Request, classify_id: int = 0, num: int = 50, start: int = 0):
    """切换"分类"视图拉笔记列表(支持分页)。classify_id 语义(实测):
    - 0  → "全部" (active + 未分类聚合)
    - >0 → 指定分类 id(必须是**笔记直属**分类 id,不递归子分类)
    - -1 → "最近删除"(trash,NAS 用 -1 作为统一 trash 桶,**没有独立 recycle 端点**)
    其他值(我试过 -2/-99/-100/-999):返回 0,不会列别的。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, "/v2/file/notepad/list", {
            "classify_id": classify_id, "start": start, "num": num, "location": 2,
        })


# ---- 历史版本(别名,canonical 名 historylist)----
@app.get("/action/notebook-historylist")
async def action_notebook_historylist(request: Request, id: int, num: int = 50):
    """历史版本列表(/v2/file/notepad/historylist,正式名)。

    ⚠️ NAS 这个端点对所有合理字段名都返回 N001212 参数有误,实测过:
       id / note_id / nid / noteId / noteid / ids[] 全部 N001212。
       不带 location → N001603 保险箱未打开(说明确实进了端点逻辑)。
       唯一可能是字段名还有别的(比如 pcweb 私有的 X-CSRF-Token 头),
       暂时没法破。要用就在 UI 上抓包看 pcweb 发啥。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, "/v2/file/notepad/historylist", {
            "id": id, "num": num, "location": 2,
        })


@app.get("/action/notebook-historyinfo")
async def action_notebook_historyinfo(request: Request, id: int, history_id: int = 0):
    """单个历史版本详情(从 historylist 拿 history_id 后用这个查内容)。
    字段:id(=笔记 id) + history_id(=历史版本 id,historylist 返回的) + location。
    返回 data.content 是历史版本的 body(可能含 HTML,经 bleach 清理)。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    body: Dict[str, Any] = {"id": id, "location": 2}
    if history_id:
        body["history_id"] = history_id
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, "/v2/file/notepad/historyinfo", body)


# ---- 搜索(别名,canonical 名 searchnotepad)----
@app.get("/action/notebook-searchnotepad")
async def action_notebook_searchnotepad(request: Request, keyword: str, num: int = 50):
    """搜索笔记(/v2/file/notepad/searchnotepad,正式名)。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, "/v2/file/notepad/searchnotepad", {
            "keyword": keyword, "num": num, "location": 2,
        })


# ---- 写配置(读有 getconfig,写也要有)----
@app.post("/action/notebook-setconfig")
async def action_notebook_setconfig(request: Request):
    """写配置(/v2/file/notepad/setconfig)。
    请求体传整个配置 JSON。读出 getconfig 看现有结构,改完再用 setconfig 写回。
    ⚠️ 字段名待精确(实测可能包含 key/value 或 json 整体)— 用先用缺字段 422 验 form 解析,
    真要写先在 UI 试。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    try:
        body = await request.json()
    except Exception:
        form = await request.form()
        body = dict(form)
    body.setdefault("location", 2)
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        return await _nas_post(client, "/v2/file/notepad/setconfig", body)


# ---- 分类树拖拽保存 ----
@app.post("/action/notebook-save-classify-tree")
async def action_notebook_save_classify_tree(request: Request, tree: str = Form(...)):
    """保存分类树(pcs 拖拽后调用)。
    ⚠️ 字段名 `tree` 暂定(实测有可能是 `classify_tree` 或 JSON 整体)— 还没在 UI 验过。
    body: tree='[{...}, ...]' 整个树 JSON 字符串,服务端原样转发。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    # 这个端点 NAS 期望可能是 JSON body 或 form,先按 form 透传
    async with httpx.AsyncClient(timeout=10, cookies=cookies) as client:
        url = _append_common_query(f"{NAS_BASE}/v2/file/notepad/save_classify_tree")
        # tree 是 JSON 字符串,location=2 必带
        r = await client.post(url, data={"tree": tree, "location": "2"})
        try:
            res = r.json()
        except Exception:
            res = {"_status": r.status_code, "_raw": r.text[:300]}
    log.info("save-classify-tree → code=%s", res.get("code"))
    return res


# ---- 笔记内嵌附件下载 ----
@app.get("/action/notebook-downloadfile")
async def action_notebook_downloadfile(request: Request, file_id: int):
    """下载笔记内嵌附件(/v2/file/notepad/downloadfile)。
    GET 形式,NAS 直接回二进制文件。透传回去,Content-Type 用 NAS 的。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return Response(content=b"not logged in", status_code=401)
    async with httpx.AsyncClient(timeout=30, cookies=cookies) as client:
        url = _append_common_query(f"{NAS_BASE}/v2/file/notepad/downloadfile")
        r = await client.get(url, params={"file_id": file_id, "location": 2})
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/octet-stream"),
        headers={"Content-Disposition": r.headers.get("content-disposition",
                     f'attachment; filename="notepad_{file_id}"')},
    )


# ---- 笔记 Word 导出 ----
@app.get("/action/notebook-downloadocx")
async def action_notebook_downloadocx(request: Request, id: int):
    """导出笔记为 Word(.docx)。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return Response(content=b"not logged in", status_code=401)
    async with httpx.AsyncClient(timeout=30, cookies=cookies) as client:
        url = _append_common_query(f"{NAS_BASE}/v2/file/notepad/downloadocx")
        r = await client.get(url, params={"id": id, "location": 2})
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        headers={"Content-Disposition": r.headers.get("content-disposition",
                     f'attachment; filename="notepad_{id}.docx"')},
    )


# ---- 笔记纯文本导出 ----
@app.get("/action/notebook-downloadt")
async def action_notebook_downloadt(request: Request, id: int):
    """导出笔记为纯文本(.txt)。"""
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return Response(content=b"not logged in", status_code=401)
    async with httpx.AsyncClient(timeout=30, cookies=cookies) as client:
        url = _append_common_query(f"{NAS_BASE}/v2/file/notepad/downloadt")
        r = await client.get(url, params={"id": id, "location": 2})
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "text/plain; charset=utf-8"),
        headers={"Content-Disposition": r.headers.get("content-disposition",
                     f'attachment; filename="notepad_{id}.txt"')},
    )


# ---- 笔记内嵌附件上传 ----
@app.post("/action/notebook-uploadfile")
async def action_notebook_uploadfile(request: Request, file: UploadFile = File(...)):
    """上传笔记内嵌附件/图片(/v2/file/notepad/uploadfile,POST octet-stream)。
    multipart 表单字段名 `file`,转发 NAS 时 NAS 期望 location=2 + file 二进制。
    ⚠️ 字段精确名待测(可能是 `file`/`data`/`content`),先用缺字段测 422。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return {"error": "not logged in"}
    blob = await file.read()
    async with httpx.AsyncClient(timeout=60, cookies=cookies) as client:
        url = _append_common_query(f"{NAS_BASE}/v2/file/notepad/uploadfile")
        # NAS 上传是 multipart: location 在 form 字段,file 在 file 字段
        r = await client.post(
            url,
            data={"location": "2"},
            files={"file": (file.filename or "upload.bin", blob, file.content_type or "application/octet-stream")},
        )
    try:
        return r.json()
    except Exception:
        return {"_status": r.status_code, "_raw": r.text[:300]}

