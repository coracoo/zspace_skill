"""下载 / 分享 / 共享服务 tool 集合(7 个读)。

源:mcp_server.py:718-763
"""
from zspace.mcp_server import main as _main
from zspace.mcp_server.main import mcp
from zspace.mcp_server.perf import _to_json


# ---- 下载 / 分享(3)----
@mcp.tool()
async def list_downloads() -> str:
    """当前下载任务列表(BT/HTTP/迅雷 等),含进度、速度、状态。"""
    return _to_json(await _main.nas.post("/downloader/list", {}))


@mcp.tool()
async def list_shares() -> str:
    """外链分享列表 + 统计(总数/过期/正常/取消)。"""
    lst = await _main.nas.post("/v2/share/list", {})
    stat = await _main.nas.post("/v2/share/statics", {})
    return _to_json({"list": lst.get("data"), "statics": stat.get("data")})


@mcp.tool()
async def list_nshares() -> str:
    """内部分享(NAS 用户之间的分享)。"""
    return _to_json(await _main.nas.post("/v2/nshare/list", {}))


# ---- 共享服务(4)----
@mcp.tool()
async def samba_status() -> str:
    """Samba/SMB 服务状态(端口、guest、host_name 等)。"""
    return _to_json(await _main.nas.post("/api/fileshare_service/samba/status", {}))


@mcp.tool()
async def webdav_status() -> str:
    """WebDAV 服务状态(http_port/https_port/status)。"""
    return _to_json(await _main.nas.post("/api/fileshare_service/webdav/status", {}))


@mcp.tool()
async def ftp_status() -> str:
    """FTP 服务状态(port、passive 范围、guest)。"""
    return _to_json(await _main.nas.post("/api/fileshare_service/ftp/status", {}))


@mcp.tool()
async def dlna_status() -> str:
    """DLNA 服务状态。"""
    return _to_json(await _main.nas.post("/api/fileshare_service/dlna/status", {}))
