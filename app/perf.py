"""SSH 性能监控(perf snapshot + 5 秒缓存)。

搬迁自 app.py:64-100, 104-233, 229-232, 397-412。逻辑不变,只是搬位置。
"""
import os
import subprocess
import time
from typing import Any, Dict

NAS_SSH_HOST = "192.168.0.135"
NAS_SSH_PORT = "57922"
NAS_SSH_USER = "<your_phone_number>"


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
    sections: Dict[str, Any] = {}
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
        mem: Dict[str, int] = {}
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
        net: Dict[str, Any] = {}
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


def get_perf_cached() -> Dict[str, Any]:
    """对外接口(原 `_get_perf_cached`):带 5 秒缓存的 SSH perf snapshot。"""
    now = time.time()
    if _perf_cache["data"] and now - _perf_cache["ts"] < _PERF_TTL:
        return _perf_cache["data"]
    data = _ssh_perf_snapshot()
    # 只缓存成功结果;失败的(含 error 键)每次重试,避免 5 秒内一直显示旧错误
    if "error" not in data:
        _perf_cache["data"] = data
        _perf_cache["ts"] = now
    return data
