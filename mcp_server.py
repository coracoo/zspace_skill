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

    async def _ensure_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15)

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

    async def _refresh_if_needed(self, response_data: dict):
        """检测 N001208(token 失效)自动重登"""
        if str(response_data.get("code")) == "N001208" and self._logged_in:
            log.warning("token expired, re-logging in")
            await self.login()

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
        await self._refresh_if_needed(data if isinstance(data, dict) else {})
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
        await self._refresh_if_needed(data if isinstance(data, dict) else {})
        return data

    async def aclose(self):
        if self._client:
            await self._client.aclose()


# ============ 全局 NasClient + 自动登录 ============
nas: NasClient  # 在 main() 里实例化


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


# ============ FastMCP Server + 26 个 Tool ============
mcp = FastMCP("zspace-nas")


# ---- 文件 ----
@mcp.tool()
async def list_files(path: str = "/sata14/my/data/") -> str:
    """列出 NAS 目录下的文件/文件夹。路径格式:/<pool>/my/<子目录>/,例如 /sata14/my/data/。
    用户只能看自己 /池名/my/ 下的内容。"""
    r = await nas.post("/v2/file/list", {
        "folderId": 0, "path": path, "start": 0, "num": 200,
        "sortby": "name", "order": "asc", "show_hidden": 0,
    })
    if r.get("code") == "200":
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
    assert nas._client is not None
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
    """极影视所有分类(电影/电视剧/动画/test 等)。"""
    return _to_json(await nas.post("/zvideo/classification/list", {}))


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


# ============ 写工具(7 个,⚠️ 真实落盘到 NAS)============
# MCP 客户端(Claude Code/Cursor)会在 LLM 调用写 tool 时弹 UI 让用户批准,
# 所以这里不再做额外 confirm。每个 tool 的 docstring 写清楚后果。


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
    return _to_json(await nas.post("/v2/file/modify", {"path": path, "newname": newname}))


@mcp.tool()
async def move(paths: str, to: str) -> str:
    """⚠️ 写入:移动文件/文件夹到目标目录。
    paths: 源路径,**多个用英文逗号分隔**,如 /a/b.txt,/c/d.txt
    to: 目标目录(必须已存在),如 /sata14/my/data/目标"""
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    return _to_json(await nas.post("/v2/file/move", {"to": to, "paths[]": path_list}))


@mcp.tool()
async def copy(paths: str, to: str) -> str:
    """⚠️ 写入:复制文件/文件夹到目标目录。
    paths: 源路径,多个用英文逗号分隔
    to: 目标目录(必须已存在)"""
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    return _to_json(await nas.post("/v2/file/copy", {"to": to, "paths[]": path_list}))


@mcp.tool()
async def remove(paths: str) -> str:
    """⚠️⚠️ 写入(危险):删除文件/文件夹,**不进回收站,不可逆**!
    paths: 要删的路径,多个用英文逗号分隔
    端点名是 /v2/file/remove(不是 delete)"""
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    return _to_json(await nas.post("/v2/file/remove", {"paths[]": path_list}))


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
    返回 N120019 = 已经关联过(也算成功)。"""
    # 字段名带 [],直接传 dict(NAS PHP 解析为数组)
    return _to_json(await nas.post("/zvideo/classification/increase", {
        "classification_id": classification_id,
        "file_path[]": file_path,
    }))


# ============ 入口 ============
async def _startup():
    global nas
    nas = NasClient()
    try:
        await nas.login()
    except Exception as e:
        log.error("startup login failed: %s", e)
        raise


def main():
    asyncio.run(_startup())
    log.info("MCP server 'zspace-nas' starting, %d tools registered", len(mcp._tool_manager._tools))
    mcp.run()


if __name__ == "__main__":
    main()
