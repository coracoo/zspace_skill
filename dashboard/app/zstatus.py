"""ZSpace NAS zstatus 解析 + 模板用 filter(jinja filters 在 main.py 注册)。

搬迁自 app.py:683-763。
"""
import re
from datetime import datetime
from typing import Any, Dict


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
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)
