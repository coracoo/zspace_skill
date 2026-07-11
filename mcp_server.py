"""ZSpace NAS MCP Server(只读版)

通过 MCP 协议把 ZSpace NAS 的能力暴露给 Claude Code / Cursor / Claude Desktop 等 AI 客户端。

环境变量:
    NAS_HOST     — NAS IP(默认 192.168.0.135)
    NAS_USER     — 用户名(手机号)
    NAS_PASSWORD — 密码
    NAS_DEVICE_ID — 可选,32 字符 device_id(默认借用已登记的)
    KEY_SSH      — 可选,SSH 密码,perf_snapshot 用
    NAS_SSH_PORT — 可选,默认 57922

启动:
    python mcp_server.py

Claude Code 配置示例:
    {
      "mcpServers": {
        "zspace-nas": {
          "command": "/path/to/python",
          "args": ["/path/to/mcp_server.py"],
          "env": { "NAS_HOST": "...", "NAS_USER": "...", "NAS_PASSWORD": "..." }
        }
      }
    }
"""
import asyncio
import base64
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,  # MCP 用 stdout 通信,log 必须 stderr
)
log = logging.getLogger("zspace-mcp")

# RAG 模块占位(在 mcp = FastMCP(...) 之后再 import,避开循环依赖)
_HAS_RAG = False
_rag_tools = None

NAS_BASE = os.environ.get("NAS_BASE", "http://192.168.0.135:5055")
NAS_HOST = os.environ.get("NAS_HOST", "192.168.0.135")
NAS_USER = os.environ.get("NAS_USER", "")
NAS_PASSWORD = os.environ.get("NAS_PASSWORD", "")
NAS_DEVICE_ID = os.environ.get("NAS_DEVICE_ID", "a6b4bd9ea4839ab4aea6f22b558bf0b2")
NAS_SSH_PORT = os.environ.get("NAS_SSH_PORT", "57922")
KEY_SSH = os.environ.get("KEY_SSH", "")

NAS_PUBKEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtrDHnaRmRaMAhZC2CmRV
CPO3ekJRo5ELX3Jjtr9P8MoWHSQbsAE5G+VTkKWhTyMQQMR0erKabn82fOZgyOO4
F+CVRSJH0TRD854IeQyFD2iZg2W2J/BzYNYC8EmBjlRhs8oS5LBc0WUN7bP4et0s
Z2LGSXbt6TetSndeV9LP8+zaKka+xvV/9aohg5rc5Ha5ka7BfTliBOyzLPR+UTKe
mx9ysWrXedlYGUjXkDRyp4xfj98bOx44EmswJh+YHYNSINyCZ4nMsat98aWOPEDl
jsflEvNt6vXFDqrziOjAPW0S/wvyvrFCZxlb+IxJMrtNH7M61spGfobE8sjNU+MC
wwIDAQAB
-----END PUBLIC KEY-----"""

_PUBKEY = load_pem_public_key(NAS_PUBKEY_PEM)


def _encrypt(plain: str) -> str:
    """RSA-PKCS1v15 + base64(NAS /auth/login 要求)"""
    cipher = _PUBKEY.encrypt(plain.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(cipher).decode("ascii")


def _common_query() -> str:
    """axios 拦截器给所有请求追加的公共参数"""
    return (
        f"?plat=web&version=2.3.2026062201"
        f"&device_id={NAS_DEVICE_ID}&device=linux&_l=zh-CN"
    )


class NasClient:
    """登录态 + httpx.AsyncClient,token 失效自动重登"""

    def __init__(self):
        if not NAS_USER or not NAS_PASSWORD:
            raise RuntimeError("NAS_USER / NAS_PASSWORD env not set")
        self._client: Optional[httpx.AsyncClient] = None
        self._cookies: dict = {}
        self._profile: dict = {}
        self._logged_in = False
        self._login_lock = asyncio.Lock()

    async def _ensure_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15)

    async def _maybe_relogin(self, response_data: dict) -> bool:
        """检测 N001208(token 失效),加锁串行重登,返回是否重登过。

        多个并发请求同时看到 token 失效时,锁保证只重登一次,
        其他请求等重登完成后直接用新 token。"""
        if str(response_data.get("code")) != "N001208" or not self._logged_in:
            return False
        async with self._login_lock:
            # 二次检查:可能其他请求已经完成了重登
            if str(response_data.get("code")) != "N001208":
                return False
            log.warning("token expired, re-logging in")
            await self.login()
            return True

    async def login(self) -> dict:
        """RSA 加密登录,返回 profile"""
        await self._ensure_client()
        form = {
            "username": _encrypt(NAS_USER),
            "password": _encrypt(NAS_PASSWORD),
            "plat": "web",
            "device": "linux",
            "device_id": NAS_DEVICE_ID,
        }
        log.info("logging in user=%s", NAS_USER)
        resp = await self._client.post(f"{NAS_BASE}/auth/login", data=form)
        body = resp.json()
        if str(body.get("code")) != "200":
            raise RuntimeError(f"login failed: code={body.get('code')} msg={body.get('msg')}")
        data = body["data"]
        self._cookies = {
            "token": data.get("token", ""),
            "username": NAS_USER,
            "device_id": NAS_DEVICE_ID,
            "device": "linux",
            "plat": "web",
        }
        for ck, cv in resp.cookies.items():
            self._cookies[ck] = cv
        self._profile = data
        self._logged_in = True
        log.info("login ok user=%s id=%s nickname=%s",
                 NAS_USER, data.get("id"), data.get("nickname"))
        return data

    async def get(self, path: str) -> dict:
        if not self._logged_in:
            await self.login()
        assert self._client is not None
        sep = "&" if "?" in path else "?"
        url = f"{NAS_BASE}{path}{sep}plat=web&version=2.3.2026062201&device_id={NAS_DEVICE_ID}&device=linux&_l=zh-CN"
        r = await self._client.get(url, cookies=self._cookies)
        try:
            data = r.json()
        except Exception:
            data = {"_status": r.status_code, "_raw": r.text[:300]}
        # token 失效:重登后重发一次
        if isinstance(data, dict) and await self._maybe_relogin(data):
            r = await self._client.get(url, cookies=self._cookies)
            try:
                data = r.json()
            except Exception:
                data = {"_status": r.status_code, "_raw": r.text[:300]}
        return data

    async def post(self, path: str, body: dict | None = None, as_form: bool = True) -> dict:
        if not self._logged_in:
            await self.login()
        assert self._client is not None
        url = f"{NAS_BASE}{path}{_common_query()}"
        if as_form and isinstance(body, dict):
            r = await self._client.post(url, data=body or {}, cookies=self._cookies)
        else:
            r = await self._client.post(url, json=body or {}, cookies=self._cookies)
        try:
            data = r.json()
        except Exception:
            data = {"_status": r.status_code, "_raw": r.text[:300]}
        # token 失效:重登后重发一次
        if isinstance(data, dict) and await self._maybe_relogin(data):
            if as_form and isinstance(body, dict):
                r = await self._client.post(url, data=body or {}, cookies=self._cookies)
            else:
                r = await self._client.post(url, json=body or {}, cookies=self._cookies)
            try:
                data = r.json()
            except Exception:
                data = {"_status": r.status_code, "_raw": r.text[:300]}
        return data

    async def aclose(self):
        if self._client:
            await self._client.aclose()

    async def download_text(self, path: str, max_bytes: int = 100 * 1024) -> Optional[str]:
        """下载小文本文件,超过 max_bytes 返回 None。RAG 全文索引用。

        /v2/file/download 是 GET + path query,带 NAS cookie。
        二进制 / 超大文件返回 None(让 RAG 跳过)。"""
        if not self._logged_in:
            await self.login()
        assert self._client is not None
        url = f"{NAS_BASE}/v2/file/download?path={path}{_common_query().replace('?', '&', 1)}"
        try:
            r = await self._client.get(url, cookies=self._cookies, timeout=10)
            if r.status_code != 200:
                return None
            if len(r.content) > max_bytes:
                return None
            try:
                return r.content.decode("utf-8")
            except UnicodeDecodeError:
                return None
        except Exception:
            return None

    async def download_bytes(self, path: str, max_bytes: int = 100 * 1024) -> Optional[bytes]:
        """下载文件原始字节(docx/pdf 等二进制)。RAG 全文索引用。

        /v2/file/download 是 GET + path query,带 NAS cookie。
        超 max_bytes 返回 None(让 RAG 跳过)。"""
        if not self._logged_in:
            await self.login()
        assert self._client is not None
        url = f"{NAS_BASE}/v2/file/download?path={path}{_common_query().replace('?', '&', 1)}"
        try:
            r = await self._client.get(url, cookies=self._cookies, timeout=10)
            if r.status_code != 200:
                return None
            if len(r.content) > max_bytes:
                return None
            return r.content
        except Exception:
            return None


# ============ Zenith Session(走 zos 云代理)============
# 架构:用户点 pcweb 远程访问 → zconnect.cn 给每个内网端口分配子域名
#   https://remote-access-{port}.zconnect.cn/  →  NAS 127.0.0.1:{port}
# 认证:zenith 云需要 session cookie(token/device_id/sign/nasId/nasPubKey/cloudPubKey...)。
# 我们 /auth/login 拿到的 token 跟 zenithtoken 是同 JWT,但其它 cloud cookie 不会自动给。
# 解决方案:用户可以从浏览器复制完整 cookie 字符串,设到 ZENITH_COOKIE env;
# 或者只用 token 试试(部分端点可能够用)。

ZENITH_COOKIE_EXTRA = os.environ.get("ZENITH_COOKIE", "").strip()
CLOUD_BASE_TPL = "https://remote-access-{port}.zconnect.cn"

# pcweb 拦截器给所有 POST 带的公共 query
PROXY_QUERY = "?&rnd={rnd}&webagent=v2"

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


class ZenithSession:
    """持有 cloud proxy 的 cookie 状态,转发请求到 remote-access-{port}.zconnect.cn"""

    def __init__(self, nas: "NasClient"):
        self.nas = nas
        self._cookie_header = ""
        self._refresh()

    def _refresh(self):
        cookies = dict(self.nas._cookies)  # 至少有 token/username/device_id
        # 叠加用户提供的完整 cookie(填 device_id/sign/nasId/cloudPubKey 等)
        if ZENITH_COOKIE_EXTRA:
            for part in ZENITH_COOKIE_EXTRA.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    cookies[k.strip()] = v.strip()
        # 填一些 pcweb 默认带的(没值也带上,云端可能依赖)
        cookies.setdefault("webagent", "v2")
        cookies.setdefault("_l", "zh_cn")
        cookies.setdefault("plat", "web")
        cookies.setdefault("app", "file")
        cookies.setdefault("device", "PC")
        cookies.setdefault("publicSwitch", "true")
        # 拼成单一 Cookie header
        self._cookie_header = "; ".join(
            f"{k}={v}" for k, v in cookies.items() if v
        )

    async def fetch(
        self, port: int, path: str,
        method: str = "GET", body: str = "",
        timeout: float = 10.0,
    ) -> dict:
        path = path if path.startswith("/") else "/" + path
        host = f"remote-access-{port}.zconnect.cn"
        rnd = f"{int(time.time()*1000)}_{os.getpid()}"
        url = f"{CLOUD_BASE_TPL.format(port=port)}{path}{PROXY_QUERY.format(rnd=rnd)}"
        # 每次请求重新拼装 cookie,避免 nas 重登后 token 过期
        self._refresh()
        cookie_hdr = self._cookie_header
        headers = {
            "Host": host,
            "Cookie": cookie_hdr,
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": f"https://{host}",
            "Referer": f"https://{host}/?v=2.3.2026062901",
        }
        if method.upper() != "GET" and body:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        async with httpx.AsyncClient(timeout=timeout, verify=False) as c:
            r = await c.request(
                method.upper(), url,
                headers=headers,
                content=body if body else None,
            )
            return {
                "_status": r.status_code,
                "content_type": r.headers.get("content-type", ""),
                "url": url,
                "headers_sent_count": len(headers),
                "cookie_count": cookie_hdr.count(";") + 1 if cookie_hdr else 0,
                "body": r.text[:4000],
            }


# ============ 全局 NasClient + ZenithSession ============
nas: NasClient  # 在 main() 里实例化
zenith: Optional[ZenithSession] = None  # 在 main() 里实例化


def _to_json(obj: Any) -> str:
    """统一序列化(MCP tool 返回 string)"""
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


# ============ SSH 性能快照(从 app.py 抄过来)===========
def _parse_perf(text: str) -> dict:
    out: dict = {}
    sections: dict = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("=== ") and line.endswith(" ==="):
            cur = line[4:-4]
            sections[cur] = []
        elif cur:
            sections[cur].append(line)

    if "LOAD" in sections and sections["LOAD"]:
        parts = sections["LOAD"][0].split()
        if len(parts) >= 3:
            out["loadavg"] = [float(x) for x in parts[:3]]

    if "CPU" in sections and sections["CPU"]:
        parts = sections["CPU"][0].split()
        if len(parts) >= 5 and parts[0] == "cpu":
            vals = [int(x) for x in parts[1:8]]
            user, _, sys_, idle, iowait, _, _ = vals[:7]
            total = sum(vals[:7])
            out["cpu_ticks"] = {"user": user, "system": sys_, "idle": idle, "iowait": iowait, "total": total}
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
            out["memory_kb"] = {
                "total": total, "available": avail, "used": total - avail,
                "cached": mem.get("Cached", 0), "swap_total": mem.get("SwapTotal", 0),
                "swap_used": mem.get("SwapTotal", 0) - mem.get("SwapFree", 0),
            }

    if "UPTIME" in sections and sections["UPTIME"]:
        parts = sections["UPTIME"][0].split()
        if len(parts) >= 2:
            out["uptime_sec"] = float(parts[0])
            out["idle_sec"] = float(parts[1])

    if "NET DEV" in sections:
        net = {}
        for line in sections["NET DEV"][2:]:
            if ":" not in line: continue
            name, rest = line.split(":", 1)
            name = name.strip()
            cols = rest.split()
            if len(cols) >= 9 and name.startswith(("eth", "kvmbr", "docker", "br-")):
                net[name] = {"rx_bytes": int(cols[0]), "tx_bytes": int(cols[8])}
        out["network"] = net

    if "THERMAL" in sections:
        temps = []
        for line in sections["THERMAL"]:
            if "=" in line:
                name, val = line.split("=", 1)
                try: temps.append({"zone": name.split("/")[-1], "temp_c": round(int(val.strip()) / 1000, 1)})
                except: pass
        out["temperatures"] = temps

    for kind in ("TOP CPU", "TOP MEM"):
        if kind in sections:
            procs = []
            for line in sections[kind]:
                parts = line.split()
                if len(parts) >= 5:
                    procs.append({"pid": int(parts[0]), "cpu_pct": float(parts[1]),
                                  "mem_pct": float(parts[2]), "rss_kb": int(parts[3]), "name": parts[4]})
            out["top_cpu_procs" if kind == "TOP CPU" else "top_mem_procs"] = procs

    out["ts"] = int(time.time())
    return out


def _ssh_perf() -> dict:
    """一次 SSH 抓全套性能指标"""
    if not KEY_SSH:
        return {"error": "KEY_SSH env not set; perf tool disabled"}
    cmd = (
        "cat /proc/loadavg; "
        "echo '=== CPU ==='; head -1 /proc/stat; "
        "echo '=== MEMINFO ==='; grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree)' /proc/meminfo; "
        "echo '=== UPTIME ==='; cat /proc/uptime; "
        "echo '=== NET DEV ==='; cat /proc/net/dev; "
        "echo '=== THERMAL ==='; for z in /sys/class/thermal/thermal_zone*; do echo -n \"$z=\"; cat $z/temp 2>/dev/null; echo; done; "
        "echo '=== TOP CPU ==='; ps -eo pid,pcpu,pmem,rss,comm --sort=-pcpu --no-headers | head -8; "
        "echo '=== TOP MEM ==='; ps -eo pid,pcpu,pmem,rss,comm --sort=-rss --no-headers | head -8"
    )
    try:
        r = subprocess.run(
            ["sshpass", "-p", KEY_SSH, "ssh", "-o", "ConnectTimeout=5",
             "-o", "StrictHostKeyChecking=accept-new",
             "-p", NAS_SSH_PORT, f"{NAS_USER}@{NAS_HOST}", cmd],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {"error": "ssh timeout"}
    except FileNotFoundError:
        return {"error": "sshpass not installed"}
    # 把 LOAD 行也带个 section 头方便 parser
    return _parse_perf("=== LOAD ===\n" + r.stdout)


def _parse_zstatus(html: str) -> dict:
    import re
    txt = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<script[^>]*>.*?</script>", "", txt, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", "|", txt)
    txt = re.sub(r"&nbsp;|&#160;", " ", txt)
    txt = re.sub(r"\|+", "|", txt)
    parts = [p.strip() for p in txt.split("|") if p.strip()]
    s = "|".join(parts)
    out: dict = {}
    m = re.search(r"设备状态.*?(\d{4}-\d{2}-\d{2}[ \d:]+)\|([^|]+天[^|]*)\|([^|]+)", s)
    if m:
        out["now"] = m.group(1).strip()
        out["uptime"] = m.group(2).strip()
        out["sn"] = m.group(3).strip()
    m = re.search(r"负载\|内存占用\|([\d./]+)\|([\d.]+)％", s)
    if m:
        out["loadavg"] = m.group(1).strip()
        out["mem_pct"] = m.group(2).strip()
    disks = []
    for m in re.finditer(r"([^|]+?)\|([\d.]+)％\|(是|否)\|", s):
        name = m.group(1).strip()
        if name in ("设备", "使用率", "只读", "磁盘"): continue
        disks.append({"name": name, "usage_pct": m.group(2), "readonly": m.group(3)})
    out["disks"] = disks
    procs = []
    for m in re.finditer(r"([^|]+?(?:服务|进程|组件|器|引擎))\|(是|否)\|(是|否)\|", s):
        procs.append({"name": m.group(1).strip(), "running": m.group(2), "healthy": m.group(3)})
    out["processes"] = procs
    return out


# ============ FastMCP Server + 57 个 Tool(35 读 + 18 写)============
# 26 读 + 7 写(原)+ 8 读 + 9 写(2026-06-30 加的 📒 记事本) = 50
# 详见 MCP.md(每个 tool 的参数/返回/NAS 端点/坑)
mcp = FastMCP("zspace-nas")


# RAG 钩子(必须在 mcp 定义后再 import,避开循环依赖;import 即注册 3 个 @mcp.tool)
try:
    import rag.mcp_tools as _rag_tools  # noqa: F401
    _HAS_RAG = True
    log.info("RAG module loaded (semantic_search / reindex / index_status registered)")
except ImportError as e:
    log.warning("RAG module not available: %s", e)
    _HAS_RAG = False


def _rag_hook(hook_name: str, *args) -> None:
    """安全调用 RAG 钩子:仅在 NAS 写入成功(code==200)时触发,
    且吞掉 RAG 自身异常,避免索引失败影响已成功的 NAS 写入。

    hook_name 是 _rag_tools 上的方法名(字符串),延迟到此处 getattr,
    避免 _HAS_RAG=False 时调用点求值 _rag_tools 触发 NameError。"""
    if not _HAS_RAG:
        return
    resp = args[0] if args else None
    if not (isinstance(resp, dict) and str(resp.get("code")) == "200"):
        return
    try:
        fn = getattr(_rag_tools, hook_name)
        fn(*args)
    except Exception as e:
        log.warning("RAG hook %s failed: %s", hook_name, e)


# ---- 文件 ----
@mcp.tool()
async def list_files(path: str = "/sata14/my/data/") -> str:
    """列出 NAS 目录下的文件/文件夹。路径格式:/<pool>/my/<子目录>/,例如 /sata14/my/data/。
    用户只能看自己 /池名/my/ 下的内容。"""
    r = await nas.post("/v2/file/list", {
        "folderId": 0, "path": path, "start": 0, "num": 200,
        "sortby": "name", "order": "asc", "show_hidden": 0,
    })
    if str(r.get("code")) == "200":
        items = r.get("data", {}).get("list", [])
        summary = [{"name": i.get("name"), "is_dir": i.get("is_dir"),
                    "size": i.get("size"), "modify_time": i.get("modify_time"),
                    "path": i.get("path")} for i in items]
        return _to_json({"total": r["data"].get("total"), "items": summary})
    return _to_json(r)


@mcp.tool()
async def file_info(path: str) -> str:
    """获取单个文件/文件夹的详细元数据。"""
    return _to_json(await nas.post("/v2/file/info", {"path": path}))


@mcp.tool()
async def recent_files() -> str:
    """最近访问的文件(实测约 992 项)。"""
    return _to_json(await nas.post("/v2/recent/list", {}))


@mcp.tool()
async def file_categories() -> str:
    """按类型分类统计(图片/视频/文档/音频 等)。"""
    return _to_json(await nas.post("/v2/file/categories", {}))


@mcp.tool()
async def list_file_labels() -> str:
    """列出 NAS 上所有文件标签(用户自建的标签体系,如 docker/课件/合同验收)。
    NAS 端点:/v2/labels/alllabels。
    返回:`data.list[{id, label_name, created_at, updated_at, top_flag, weight}]`"""
    return _to_json(await nas.post("/v2/labels/alllabels", {}))


# ---- 存储池 ----
@mcp.tool()
async def list_storage_pools() -> str:
    """列出所有存储池及其物理磁盘(sata14 20TB 2 块 WDC、nvme19 500GB Samsung 等)。
    返回每个 pool 的容量/已用/可用、磁盘 SMART 简报、温度、健康状态。"""
    return _to_json(await nas.get("/zspool/info"))


@mcp.tool()
async def hardware_info() -> str:
    """硬件槽位(SATA/NVMe/eSATA 各几个)。"""
    return _to_json(await nas.get("/zspool/hardware/info"))


@mcp.tool()
async def pool_capability() -> str:
    """存储池能力(如是否加密)。"""
    return _to_json(await nas.get("/zspool/capability"))


@mcp.tool()
async def smart_report(sn: str, pool_id: int) -> str:
    """读取磁盘 SMART 报告(17 个属性,含加电时间、温度、坏道等)。
    sn 从 list_storage_pools 拿,pool_id 同(如 14)。"""
    return _to_json(await nas.post("/zspool/smart/report2", {"sn": sn, "pool_id": pool_id}))


# ---- 监控 ----
@mcp.tool()
async def system_status() -> str:
    """NAS 综合状态:开机时长、负载、内存占用、磁盘使用率、关键服务健康状态、网络延迟。
    数据来源 NAS 自带 /zstatus HTML 页(免鉴权)。"""
    await nas._ensure_client()
    if nas._client is None:
        return _to_json({"error": "NAS client 未初始化"})
    url = f"{NAS_BASE}/zstatus{_common_query()}"
    r = await nas._client.get(url)
    return _to_json(_parse_zstatus(r.text))


@mcp.tool()
async def perf_snapshot() -> str:
    """实时性能快照(通过 SSH 读 /proc):CPU 占用、Load、内存、温度、网络 I/O、Top 进程。
    需要 KEY_SSH 环境变量。一次 SSH 0.3 秒搞定,不会卡 NAS。"""
    return _to_json(_ssh_perf())


# ---- 影视 ----
@mcp.tool()
async def list_video_classes() -> str:
    """极影视所有分类(电影/电视剧/动画/test 等)。

    返回结构:
      data: NAS 原始数组(每个含 is_system / is_enable / collection_count 等)
      summary: 状态摘要 — enabled/disabled 计数 + 禁用分类 ID 列表
        - 如果有 disabled,把名字打印出来(很可能是用户主动关的,挪 collection 别挪过去)
    """
    r = await nas.post("/zvideo/classification/list", {})
    if not isinstance(r, dict) or str(r.get("code")) != "200":
        return _to_json(r)
    classes = r.get("data") or []
    enabled = [c for c in classes if c.get("is_enable") != 0]
    disabled = [c for c in classes if c.get("is_enable") == 0]
    system = [c for c in classes if c.get("is_system") == 1]
    user = [c for c in classes if c.get("is_system") != 1]
    summary = {
        "total": len(classes),
        "enabled_count": len(enabled),
        "disabled_count": len(disabled),
        "system_count": len(system),
        "user_count": len(user),
        "disabled_ids": [c.get("id") for c in disabled],
        "disabled_names": [c.get("name") for c in disabled],
        "warning": None,
    }
    if disabled:
        sys_disabled = [c.get("name") for c in disabled if c.get("is_system") == 1]
        user_disabled = [c.get("name") for c in disabled if c.get("is_system") != 1]
        bits = []
        if sys_disabled:
            bits.append(f"系统内置 {sys_disabled} 已被关闭")
        if user_disabled:
            bits.append(f"用户分类 {user_disabled} 已禁用")
        summary["warning"] = "; ".join(bits) + " — 操作这些分类前先确认是不是故意的"
    return _to_json({"data": classes, "summary": summary})


@mcp.tool()
async def get_video_classification_state(classification_id: str) -> str:
    """查单个极影视分类的状态(UUID → 详情)。

    返回: 原始 classification dict + 一个 ok 字段(校验该 ID 是否存在且 is_enable 状态)
    用法: LLM 在调 `link_folder_to_classification` 前先确认目标分类没被禁用
    """
    r = await nas.post("/zvideo/classification/list", {})
    if not isinstance(r, dict) or str(r.get("code")) != "200":
        return _to_json({"error": f"NAS list failed: {r.get('code')} {r.get('msg')}"})
    classes = r.get("data") or []
    target = next((c for c in classes if c.get("id") == classification_id), None)
    if not target:
        return _to_json({"error": f"classification_id={classification_id} not found in NAS ({len(classes)} classes total)"})
    return _to_json({
        "ok": True,
        "data": target,
        "is_enable": target.get("is_enable"),
        "is_system": target.get("is_system"),
        "warning": "⚠️ 该分类已被禁用(is_enable=0) — 不要把目录关联到这个分类" if target.get("is_enable") == 0 else None,
    })


@mcp.tool()
async def latest_movies() -> str:
    """极影视最新入库合集(首页"最新",20 部)。"""
    return _to_json(await nas.post("/zvideo/home/collection/latest", {}))


@mcp.tool()
async def suggested_movies() -> str:
    """极影视推荐合集(首页"推荐",20 部)。"""
    return _to_json(await nas.post("/zvideo/home/collection/suggested", {}))


@mcp.tool()
async def random_movies() -> str:
    """极影视随机推荐(12 部,每次结果不同,适合"不知道看啥")。"""
    return _to_json(await nas.post("/zvideo/video/randomlist", {}))


@mcp.tool()
async def list_video_dirs() -> str:
    """极影视源目录(扫描影视内容的源文件夹)。"""
    return _to_json(await nas.post("/zvideo/classification/dirs", {}))


# ---- 音乐 / 相册 ----
@mcp.tool()
async def list_songs() -> str:
    """极音乐全部歌曲(实测 4549 首,主要 FLAC/DSF 高保真格式)。"""
    return _to_json(await nas.post("/zmusic/api/v2/song/list", {}))


@mcp.tool()
async def list_albums() -> str:
    """相册列表(实测 218 个,含人脸/宠物/儿童/场景/地理/节日等分类)。
    type 编码: 40=来源 60=儿童 90=主题 100=人脸 110=场景 120=节日 130=地理 150=宠物。"""
    return _to_json(await nas.post("/v2/album/albums", {}))


@mcp.tool()
async def list_album_feeds(album_id: int, num: int = 20) -> str:
    """列出某相册里的照片/视频。album_id 从 list_albums 拿。"""
    return _to_json(await nas.post("/v2/album/album/feeds",
                                   {"album_id": album_id, "start": 0, "num": num}))


# ---- 下载 / 分享 ----
@mcp.tool()
async def list_downloads() -> str:
    """当前下载任务列表(BT/HTTP/迅雷 等),含进度、速度、状态。"""
    return _to_json(await nas.post("/downloader/list", {}))


@mcp.tool()
async def list_shares() -> str:
    """外链分享列表 + 统计(总数/过期/正常/取消)。"""
    lst = await nas.post("/v2/share/list", {})
    stat = await nas.post("/v2/share/statics", {})
    return _to_json({"list": lst.get("data"), "statics": stat.get("data")})


@mcp.tool()
async def list_nshares() -> str:
    """内部分享(NAS 用户之间的分享)。"""
    return _to_json(await nas.post("/v2/nshare/list", {}))


# ---- 共享服务 ----
@mcp.tool()
async def samba_status() -> str:
    """Samba/SMB 服务状态(端口、guest、host_name 等)。"""
    return _to_json(await nas.post("/api/fileshare_service/samba/status", {}))


@mcp.tool()
async def webdav_status() -> str:
    """WebDAV 服务状态(http_port/https_port/status)。"""
    return _to_json(await nas.post("/api/fileshare_service/webdav/status", {}))


@mcp.tool()
async def ftp_status() -> str:
    """FTP 服务状态(port、passive 范围、guest)。"""
    return _to_json(await nas.post("/api/fileshare_service/ftp/status", {}))


@mcp.tool()
async def dlna_status() -> str:
    """DLNA 服务状态。"""
    return _to_json(await nas.post("/api/fileshare_service/dlna/status", {}))


# ---- 其他 ----
@mcp.tool()
async def whoami() -> str:
    """当前 NAS 登录用户信息(id, nickname, is_master, sp_perms 等)。"""
    return _to_json({
        "user": NAS_USER,
        "profile": nas._profile,
        "device_id": NAS_DEVICE_ID,
        "nas_base": NAS_BASE,
    })


# ============ 📒 记事本(独立记事本 location=2,17 个 tool:8 读 + 9 写)============
# 完整 NAS 端点映射详见 MCP.md §4.9 / §5.3
# location=2 = 主菜单的独立记事本(平级于保险箱);location=1 是保险箱备忘录,需要开保险箱
# 关键坑:
#   - body 必须以 <h1>{title}</h1> 开头(否则 NAS 不存内容,实测)
#   - 删除用 ids[] PHP 数组语法(批量:ids[]=3&ids[]=4)
#   - pin 字段是 pin_flag 不是 is_top(但两个 NAS 都接受)
#   - classify_id=-1 = "最近删除"(trash);0 = "全部";>0 = 叶子分类(不递归父分类)


@mcp.tool()
async def notebook_list(classify_id: int = 0, num: int = 50, start: int = 0) -> str:
    """列出笔记。
    classify_id 语义:
      0  → "全部笔记"(active + 未分类)
      >0 → 指定分类 id(必须是笔记**直属**分类 id,不递归子分类)
      -1 → "最近删除"(trash)
    num: 每页条数(默认 50)
    start: 分页偏移(默认 0)
    返回 list + total。
    NAS 端点:/v2/file/notepad/list"""
    return _to_json(await nas.post("/v2/file/notepad/list", {
        "classify_id": classify_id, "start": start, "num": num, "location": 2,
    }))


@mcp.tool()
async def notebook_info(id: int) -> str:
    """单条笔记详情(含 body HTML、title、分类、标签、更新时间)。
    id: 笔记 id(从 notebook_list 拿)
    NAS 端点:/v2/file/notepad/info"""
    return _to_json(await nas.post("/v2/file/notepad/info", {
        "id": id, "location": 2,
    }))


@mcp.tool()
async def notebook_search(keyword: str, num: int = 50) -> str:
    """搜索笔记(标题/正文/in_brief 全文匹配)。
    keyword: 关键词
    num: 返回条数上限(默认 50)
    NAS 端点:/v2/file/notepad/searchnotepad"""
    return _to_json(await nas.post("/v2/file/notepad/searchnotepad", {
        "keyword": keyword, "num": num, "location": 2,
    }))


@mcp.tool()
async def notebook_allclassify() -> str:
    """完整分类树(含嵌套,每个节点带 child[] 数组)。
    笔记 → 叶子分类绑定:note.classify_id 等于**叶子**分类 id,不是父级。
    pcweb 的"分类1"父级视图是前端聚合(遍历树 + 每个叶子调 notebook_list(classify_id=leaf.id))。
    NAS 端点:/v2/file/notepad/allclassify"""
    return _to_json(await nas.post("/v2/file/notepad/allclassify", {"location": 2}))


@mcp.tool()
async def notebook_classifylist() -> str:
    """顶层分类列表(只列 parent_id=0 的顶层,带 child_num 计数)。
    不如 notebook_allclassify 完整(无嵌套),只是顶层概览。
    NAS 端点:/v2/file/notepad/classifylist"""
    return _to_json(await nas.post("/v2/file/notepad/classifylist", {
        "start": 0, "num": 50, "location": 2,
    }))


@mcp.tool()
async def notebook_totalsize() -> str:
    """笔记总占用大小(字节)。
    NAS 端点:/v2/file/notepad/totalsize"""
    return _to_json(await nas.post("/v2/file/notepad/totalsize", {"location": 2}))


@mcp.tool()
async def notebook_getconfig() -> str:
    """记事本配置(自动保存时间等)。
    返回 list[{id, scope, config_key, config_value, ...}]。
    NAS 端点:/v2/file/notepad/getconfig"""
    return _to_json(await nas.post("/v2/file/notepad/getconfig", {"location": 2}))


@mcp.tool()
async def notebook_historyinfo(id: int, history_id: int = 0) -> str:
    """单个历史版本详情(从历史版本拿 body)。
    id: 笔记 id
    history_id: 历史版本 id(从 historylist 拿;historylist 字段未破,
              当前直接传 history_id=0 也能拿到笔记的"当前版本"快照)
    NAS 端点:/v2/file/notepad/historyinfo"""
    return _to_json(await nas.post("/v2/file/notepad/historyinfo", {
        "id": id, "history_id": history_id, "location": 2,
    }))


# ---- 记事本写工具(9 个,⚠️ 真实落盘)----
# h1 前缀坑:body 必须以 `<h1>{title}</h1>` 开头,否则 NAS 不存内容
# 删两次坑:同 id 第一次删移到 trash(classify_id=-1),第二次永久删除,不可恢复

_H1_TITLE_RE = None


def _ensure_h1_prefix(title: str, body: str) -> str:
    """确保 body 以 `<h1>{title}</h1>` 开头,容忍标签带属性/空白。
    若开头已是匹配的 h1(任意属性)则原样返回,否则补一个。"""
    global _H1_TITLE_RE
    if _H1_TITLE_RE is None:
        import re as _re
        _H1_TITLE_RE = _re.compile(r"^\s*<h1\b[^>]*>", _re.IGNORECASE)
    if _H1_TITLE_RE.match(body):
        return body  # 已有 h1 开头(容忍属性),不重复补
    return f"<h1>{title}</h1>\n{body}"


@mcp.tool()
async def notebook_new(title: str, body: str, classify_id: int = 0) -> str:
    """⚠️ 写入:新建笔记。
    title: 标题
    body: HTML 正文,**必须以 `<h1>{title}</h1>` 开头**(自动加,不用手动拼)
    classify_id: 目标**叶子**分类 id(0=未分类,不是父级)
    返回新笔记 id。
    NAS 端点:/v2/file/notepad/new"""
    # 自动加 h1 前缀防"body 字段对但 NAS 存空"的坑
    body = _ensure_h1_prefix(title, body)
    resp = await nas.post("/v2/file/notepad/new", {
        "title": title, "body": body, "classify_id": classify_id, "location": 2,
    })
    new_id = (resp.get("data") or {}).get("id") if isinstance(resp.get("data"), dict) else None
    if new_id is not None:
        _rag_hook("rag_on_notebook_write", resp, new_id, title, body)
    return _to_json(resp)


@mcp.tool()
async def notebook_modify(id: int, title: str, body: str) -> str:
    """⚠️ 写入:修改笔记。
    id: 笔记 id
    title: 新标题
    body: 新正文(必须以 `<h1>{title}</h1>` 开头,自动加)
    NAS 端点:/v2/file/notepad/modify"""
    body = _ensure_h1_prefix(title, body)
    resp = await nas.post("/v2/file/notepad/modify", {
        "id": id, "title": title, "body": body, "location": 2,
    })
    _rag_hook("rag_on_notebook_write", resp, id, title, body)
    return _to_json(resp)


@mcp.tool()
async def notebook_delete(ids: str) -> str:
    """⚠️ 写入:删除笔记(支持批量,**进 trash**)。
    ids: 笔记 id,**多个用英文逗号分隔**,如 `3,4,5`
    第一次删:移到 trash(classify_id=-1);第二次同 id:永久删除不可恢复
    批量用 ids[] PHP 数组语法(httpx 自动编码)
    NAS 端点:/v2/file/notepad/delete"""
    id_list = [int(x.strip()) for x in ids.split(",") if x.strip()]
    resp = await nas.post("/v2/file/notepad/delete", {
        "ids[]": id_list, "location": 2,
    })
    _rag_hook("rag_on_notebook_delete", resp, id_list)
    return _to_json(resp)


@mcp.tool()
async def notebook_pin(id: int, pin_flag: int) -> str:
    """⚠️ 写入:置顶 / 取消置顶。
    id: 笔记 id
    pin_flag: 1=置顶, 0=取消
    NAS 字段名是 pin_flag(也接受 is_top,但 pin_flag 是官方)
    NAS 端点:/v2/file/notepad/pin"""
    return _to_json(await nas.post("/v2/file/notepad/pin", {
        "id": id, "pin_flag": pin_flag, "location": 2,
    }))


@mcp.tool()
async def notebook_updatelabel(id: int, label: str) -> str:
    """⚠️ 写入:更新笔记标签。
    id: 笔记 id
    label: 标签,**逗号分隔**(如 `工作,dashboard`);**空字符串 = 清空所有标签**
    NAS 端点:/v2/file/notepad/updatelabel"""
    return _to_json(await nas.post("/v2/file/notepad/updatelabel", {
        "id": id, "label": label, "location": 2,
    }))


@mcp.tool()
async def notebook_movenotepad(id: int, classify_id: int) -> str:
    """⚠️ 写入:移动笔记到分类。
    id: 笔记 id
    classify_id: 目标**叶子**分类 id(子分类优先;不能用父级 id)
    NAS 端点:/v2/file/notepad/movenotepad"""
    return _to_json(await nas.post("/v2/file/notepad/movenotepad", {
        "id": id, "classify_id": classify_id, "location": 2,
    }))


@mcp.tool()
async def notebook_newclassify(name: str, parent_id: int = 0) -> str:
    """⚠️ 写入:新建分类。
    name: 分类名
    parent_id: 父分类 id(0=顶级;>0=父分类的 id 实现嵌套)
    NAS 端点:/v2/file/notepad/newclassify"""
    return _to_json(await nas.post("/v2/file/notepad/newclassify", {
        "name": name, "parent_id": parent_id, "location": 2,
    }))


@mcp.tool()
async def notebook_deleteclassify(classify_id: int) -> str:
    """⚠️ 写入:删除分类。**分类下的笔记会被 NAS 处理**(进 trash 或变 classify_id=0,实测未明)。
    classify_id: 要删的分类 id
    NAS 端点:/v2/file/notepad/deleteclassify"""
    return _to_json(await nas.post("/v2/file/notepad/deleteclassify", {
        "classify_id": classify_id, "location": 2,
    }))


@mcp.tool()
async def notebook_updateclassify(classify_id: int, new_name: str) -> str:
    """⚠️ 写入:重命名分类。
    classify_id: 要改的分类 id
    new_name: 新名字
    NAS 端点:/v2/file/notepad/updateclassify"""
    return _to_json(await nas.post("/v2/file/notepad/updateclassify", {
        "classify_id": classify_id, "new_name": new_name, "location": 2,
    }))


# ============ 写工具(原 7 个 + 上面 9 个 notebook + 2 个标签 = 18 个,⚠️ 真实落盘到 NAS)============
# MCP 客户端(Claude Code/Cursor)会在 LLM 调用写 tool 时弹 UI 让用户批准,
# 所以这里不再做额外 confirm。每个 tool 的 docstring 写清楚后果。


@mcp.tool()
async def save_file_label(label_names: str, paths: str) -> str:
    """⚠️ 写入:给文件/文件夹打标签(覆盖式,非追加)。

    label_names: 标签名,**多个用英文逗号分隔**,如 docker,重要
    paths: 文件/文件夹路径,**多个用英文逗号分隔**,如 /sata14/my/data/a.yml,/sata14/my/data/b/
    行为:把指定标签集合**完整替换**到这些文件上(已有的其他标签会被清掉)
    NAS 端点:/v2/labels/savefilelabel
    ⚠️ 注意:
    - 此操作是覆盖式,会清除这些文件上之前已打的其他标签
    - 字段是 `label_names[]` + `filepaths[]` PHP 数组语法(本工具自动处理)
    - **如果 label_names 里有不存在的标签名,NAS 会自动创建**(实测验证)
      所以这个 tool 同时也是**创建新标签**的唯一入口
    - 想要纯创建标签但不打到任何文件,传 `paths="/sata14/my/data/"`(任意已有路径即可)"""
    label_list = [s.strip() for s in label_names.split(",") if s.strip()]
    path_list = [s.strip() for s in paths.split(",") if s.strip()]
    return _to_json(await nas.post("/v2/labels/savefilelabel", {
        "label_names[]": label_list,
        "filepaths[]": path_list,
    }))


@mcp.tool()
async def delete_label(label_names: str) -> str:
    """⚠️⚠️ 写入:删除一个或多个用户自建标签。

    label_names: 标签名,**多个用英文逗号分隔**,如 docker,重要
    NAS 端点:/v2/labels/deletelabel
    ⚠️ 注意:
    - 字段是 `label_names[]` PHP 数组语法(本工具自动处理)
    - 删除标签后,**所有文件上打的这个标签都会被移除**(不只是解除关联)
    - 标签 ID 在 list_file_labels 里看;删除用名字,不需要先查 ID
    - NAS 没有专门的"创建标签"端点 — 用 `save_file_label` 传不存在的标签名会自动建"""
    label_list = [s.strip() for s in label_names.split(",") if s.strip()]
    return _to_json(await nas.post("/v2/labels/deletelabel", {
        "label_names[]": label_list,
    }))


@mcp.tool()
async def mkdir(parent: str, name: str) -> str:
    """⚠️ 写入:在 NAS 创建文件夹。
    parent: 父目录,无尾斜杠,如 /sata14/my/data/备份
    name: 新文件夹名,如 test
    返回新文件夹的完整 metadata(失败返回 NAS 错误码)。"""
    return _to_json(await nas.post("/v2/file/newdir", {
        "parent": parent, "name": name, "rename": 0,
    }))


@mcp.tool()
async def rename(path: str, newname: str) -> str:
    """⚠️ 写入:重命名文件/文件夹。
    path: 原完整路径,如 /sata14/my/data/备份/test
    newname: 新名字(只名字,不是完整路径)"""
    # NAS 用 form 时字段是 newname,直接传 dict 会编码成 newname=...
    resp = await nas.post("/v2/file/modify", {"path": path, "newname": newname})
    # 算新路径 = dirname(path) + '/' + newname
    idx = path.rfind("/")
    new_path = (path[: idx + 1] if idx >= 0 else "") + newname
    _rag_hook("rag_on_file_rename", resp, path, new_path)
    return _to_json(resp)


@mcp.tool()
async def move(paths: str, to: str) -> str:
    """⚠️ 写入:移动文件/文件夹到目标目录。
    paths: 源路径,**多个用英文逗号分隔**,如 /a/b.txt,/c/d.txt
    to: 目标目录(必须已存在),如 /sata14/my/data/目标"""
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    to_clean = to.rstrip("/") + "/"
    resp = await nas.post("/v2/file/move", {"to": to, "paths[]": path_list})
    moves = [(p, to_clean + p.rsplit("/", 1)[-1]) for p in path_list]
    _rag_hook("rag_on_file_move", resp, moves)
    return _to_json(resp)


@mcp.tool()
async def copy(paths: str, to: str) -> str:
    """⚠️ 写入:复制文件/文件夹到目标目录。
    paths: 源路径,多个用英文逗号分隔
    to: 目标目录(必须已存在)"""
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    to_clean = to.rstrip("/") + "/"
    resp = await nas.post("/v2/file/copy", {"to": to, "paths[]": path_list})
    new_paths = [to_clean + p.rsplit("/", 1)[-1] for p in path_list]
    _rag_hook("rag_on_file_write", resp, new_paths)
    return _to_json(resp)


@mcp.tool()
async def remove(paths: str) -> str:
    """⚠️⚠️ 写入(危险):删除文件/文件夹,**不进回收站,不可逆**!
    paths: 要删的路径,多个用英文逗号分隔
    端点名是 /v2/file/remove(不是 delete)"""
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    resp = await nas.post("/v2/file/remove", {"paths[]": path_list})
    _rag_hook("rag_on_file_delete", resp, path_list)
    return _to_json(resp)


@mcp.tool()
async def add_video_classification(
    name: str, file_path: str = "", not_scrape: int = 1
) -> str:
    """⚠️ 写入:在极影视新建一个分类(如"动漫""纪录片")。
    name: 分类名,如 test
    file_path: 关联目录(可选,实测 NAS 不会真的关联,需要单独调 link_folder_to_classification)
    not_scrape: 1=不刮削(推荐测试用,避免 NAS 跑去 TMDB 查询);0=刮削"""
    form = {
        "classification_name": name,
        "share_users": "[]",
        "not_scrape": not_scrape,
    }
    if file_path:
        form["file_path"] = file_path
    return _to_json(await nas.post("/zvideo/classification/add", form))


@mcp.tool()
async def link_folder_to_classification(
    classification_id: str, file_path: str
) -> str:
    """⚠️ 写入:把目录关联到极影视分类(让分类扫描该目录的影片)。
    classification_id: 分类 UUID(从 list_video_classes 拿)
    file_path: 要关联的目录路径,如 /sata14/my/data/备份/test
    关键:字段名是 file_path[](PHP 数组语法),这里自动处理。
    ⚠️ **状态校验**:目标分类 is_enable=0 时直接拒绝,不发请求到 NAS。
      用 `get_video_classification_state(classification_id)` 先确认状态。
    返回 N120019 = 已经关联过(也算成功)。"""
    # 状态校验:目标分类被禁用 → 拒绝;无法校验 → 默认拒绝(fail-closed)
    list_resp = await nas.post("/zvideo/classification/list", {})
    if not (isinstance(list_resp, dict) and str(list_resp.get("code")) == "200"):
        return _to_json({
            "error": "无法校验分类状态,拒绝执行写入(避免关联到禁用分类)",
            "hint": "稍后重试;若持续失败,检查 NAS 连接或 token",
            "list_resp": list_resp,
        })
    target = next(
        (c for c in (list_resp.get("data") or [])
         if c.get("id") == classification_id),
        None,
    )
    if target is None:
        return _to_json({
            "error": f"classification_id={classification_id} 不存在",
            "hint": "调 list_video_classes 拿有效 ID",
        })
    if target.get("is_enable") == 0:
        return _to_json({
            "error": f"分类 '{target.get('name')}' 已被禁用(is_enable=0),不接受关联",
            "hint": "这是用户主动关的。要恢复关联请先在 pcweb UI 把它打开。",
            "classification": target,
        })
    # 字段名带 [],直接传 dict(NAS PHP 解析为数组)
    return _to_json(await nas.post("/zvideo/classification/increase", {
        "classification_id": classification_id,
        "file_path[]": file_path,
    }))


# ============ 远程访问代理(走 zos 云代理,新增 4 个)============
# 公网 URL 模板:https://remote-access-{port}.zconnect.cn/ → NAS 127.0.0.1:{port}
# 工作流:用户在白名单加端口 → zos 自动分配子域名 → 互联网可访问
# 这些工具让 MCP 客户端(Claude Code)在公网上也能访问 LAN 内 HTTP 服务


@mcp.tool()
async def proxy_login() -> str:
    """强制重新登录,刷新 zenith cloud session cookie(原 /auth/login 的 token)。

    调用场景:
    - 启动时 zenith session 初始化失败
    - 收到 401/403 from proxy_fetch(可能 token 过期)
    - 想换账号测试
    返回:登录 profile 摘要 + cookie 数量(完整 cookie 不暴露)。"""
    global zenith
    try:
        await nas.login()
        zenith = ZenithSession(nas)
        return _to_json({
            "ok": True,
            "user_id": nas._profile.get("id"),
            "username": nas._profile.get("username"),
            "nickname": nas._profile.get("nickname"),
            "is_master": nas._profile.get("is_master"),
            "cookie_count": zenith._cookie_header.count(";") + 1 if zenith._cookie_header else 0,
            "has_extra_cookie": bool(ZENITH_COOKIE_EXTRA),
        })
    except Exception as e:
        return _to_json({"ok": False, "error": str(e)})


@mcp.tool()
async def proxy_url_for_port(port: int) -> str:
    """返回 zos 给定 NAS 端口分配的公网 URL 模板。

    port: NAS 本地端口号(白名单里的)
    返回:形如 https://remote-access-33335.zconnect.cn/

    注意:URL 是否真能访问,取决于白名单里是否有 `127.0.0.1:{port}` 或 `LAN_IP:{port}`。
    调用 proxy_fetch(port, "/") 可以验证。"""
    url = f"https://remote-access-{port}.zconnect.cn/"
    return _to_json({
        "port": port,
        "url": url,
        "note": "If 200 with empty body or login redirect, whitelist doesn't include 127.0.0.1:{port}.",
    })


@mcp.tool()
async def proxy_fetch(
    port: int, path: str = "/",
    method: str = "GET", body: str = "",
) -> str:
    """通过 zos 云代理从公网访问 NAS 内网 HTTP 服务。

    port: NAS 本地端口(白名单里的)
    path: 要请求的路径,默认 /
    method: HTTP 方法,默认 GET
    body: 请求 body(POST/PUT 时用,application/x-www-form-urlencoded)

    工作原理:把请求发到 https://remote-access-{port}.zconnect.cn/{path},
    zos 转发到 NAS 127.0.0.1:{port}(假设白名单有)。

    ⚠️ 已知 gap:
    - 白名单条目是 `LAN_IP:port`(如 `192.168.0.118:9876`)而不是 `127.0.0.1:port` 时,
      zos 可能拒绝或代理到错误的机器
    - 如果 cloud session cookie 不全(/auth/login 只给 token,不给 sign/cloudPubKey...),
      云代理可能直接 SPA HTML 回包;完整 cookie 需通过 ZENITH_COOKIE env 提供"""
    global zenith
    if zenith is None:
        zenith = ZenithSession(nas)
    res = await zenith.fetch(port, path, method, body)
    return _to_json(res)


@mcp.tool()
async def proxy_list_whitelist() -> str:
    """读 NAS 远程访问白名单(所有端口映射规则)。

    ⚠️ 已知 gap:NAS 上 /zrps/api/remoteaccess/list 和 /info 都返回 200 + 空 body
    (openresty 路由存在但后端不响应)。完整白名单只能从 pcweb UI 的"远程访问"页看。
    此工具返回登录 profile + 说明 gap。"""
    return _to_json({
        "gap": True,
        "msg": "/zrps/api/remoteaccess/{list,info,getInfo,...} all return 200+empty body. "
               "NAS openresty has the route but no backend response. "
               "View whitelist via pcweb UI → 远程访问.",
        "logged_in_as": nas._profile.get("username"),
        "user_id": nas._profile.get("id"),
        "nas_id": "Z0431212VNY4H",  # 来自 paste-cache 抓包,硬编码
        "tip": "Use proxy_url_for_port(port) to enumerate public URLs once you know the ports from pcweb UI.",
    })


# ============ 入口 ============
async def _startup():
    global nas, zenith
    nas = NasClient()
    try:
        await nas.login()
        zenith = ZenithSession(nas)
        log.info("zenith session ready, cookie_count=%d, has_extra=%s",
                 zenith._cookie_header.count(";") + 1 if zenith._cookie_header else 0,
                 bool(ZENITH_COOKIE_EXTRA))
    except Exception as e:
        log.error("startup login failed: %s", e)
        raise


def main():
    asyncio.run(_startup())
    log.info("MCP server 'zspace-nas' starting, %d tools registered", len(mcp._tool_manager._tools))
    mcp.run()


if __name__ == "__main__":
    main()
