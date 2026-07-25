"""性能 & 状态采集辅助函数(从 app.py 抄过来,纯函数无依赖)。

包含:
- `_to_json`      — MCP tool 统一序列化(返回 string)
- `_parse_perf`   — 解析 SSH 抓回来的 /proc 块
- `_ssh_perf`     — 一次 SSH 抓全套性能指标
- `_parse_zstatus`— 解析 NAS 自带 /zstatus HTML 页
"""
import json
import logging
import os
import subprocess
import time
from typing import Any

log = logging.getLogger("zspace-mcp")

# SSH 性能快照依赖的环境变量(读 env,默认与原 mcp_server.py 一致)
NAS_HOST = os.environ.get("NAS_HOST", "")
NAS_USER = os.environ.get("NAS_USER", "")
NAS_SSH_PORT = os.environ.get("NAS_SSH_PORT", "57922")
KEY_SSH = os.environ.get("KEY_SSH", "")


def _to_json(obj: Any) -> str:
    """统一序列化(MCP tool 返回 string)"""
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


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
