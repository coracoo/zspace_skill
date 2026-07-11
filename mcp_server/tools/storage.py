"""存储池 + 监控 tool 集合(8 个):4 存储池读 + system_status + perf_snapshot + whoami。

源:mcp_server.py:563-609(存储池/监控) + 764-783(whoami)
"""
from mcp_server import main as _main
from mcp_server.main import mcp
from mcp_server.perf import _to_json, _parse_zstatus, _ssh_perf
from nas.proto import NAS_BASE, common_query


# ---- 存储池(4 读)----
@mcp.tool()
async def list_storage_pools() -> str:
    """列出所有存储池及其物理磁盘(sata14 20TB 2 块 WDC、nvme19 500GB Samsung 等)。
    返回每个 pool 的容量/已用/可用、磁盘 SMART 简报、温度、健康状态。"""
    return _to_json(await _main.nas.get("/zspool/info"))


@mcp.tool()
async def hardware_info() -> str:
    """硬件槽位(SATA/NVMe/eSATA 各几个)。"""
    return _to_json(await _main.nas.get("/zspool/hardware/info"))


@mcp.tool()
async def pool_capability() -> str:
    """存储池能力(如是否加密)。"""
    return _to_json(await _main.nas.get("/zspool/capability"))


@mcp.tool()
async def smart_report(sn: str, pool_id: int) -> str:
    """读取磁盘 SMART 报告(17 个属性,含加电时间、温度、坏道等)。
    sn 从 list_storage_pools 拿,pool_id 同(如 14)。"""
    return _to_json(await _main.nas.post("/zspool/smart/report2", {"sn": sn, "pool_id": pool_id}))


# ---- 监控(2)----
@mcp.tool()
async def system_status() -> str:
    """NAS 综合状态:开机时长、负载、内存占用、磁盘使用率、关键服务健康状态、网络延迟。
    数据来源 NAS 自带 /zstatus HTML 页(免鉴权)。"""
    nas = _main.nas
    await nas._ensure_client()
    if nas._client is None:
        return _to_json({"error": "NAS client 未初始化"})
    url = f"{NAS_BASE}/zstatus{common_query(nas._device_id)}"
    r = await nas._client.get(url)
    return _to_json(_parse_zstatus(r.text))


@mcp.tool()
async def perf_snapshot() -> str:
    """实时性能快照(通过 SSH 读 /proc):CPU 占用、Load、内存、温度、网络 I/O、Top 进程。
    需要 KEY_SSH 环境变量。一次 SSH 0.3 秒搞定,不会卡 NAS。"""
    return _to_json(_ssh_perf())


# ---- 其他(1)----
@mcp.tool()
async def whoami() -> str:
    """当前 NAS 登录用户信息(id, nickname, is_master, sp_perms 等)。"""
    import os
    return _to_json({
        "user": os.environ.get("NAS_USER", ""),
        "profile": _main.nas._profile,
        "device_id": _main.nas._device_id,
        "nas_base": NAS_BASE,
    })
