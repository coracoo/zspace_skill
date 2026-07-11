#!/usr/bin/env python3
"""file_organizer.py — NAS 文件诊断(只读)。

复用 media-organizer 的同步桥接模式,扫 NAS 找:
  - 重复文件(按 (size, ext) 弱指纹分组)
  - 孤儿文件(既不在影视源目录下,也没打标签)

不动 NAS,只生成报告。

⚠️ **N150 性能约束**:
  - 单线程顺序扫描(无 concurrent.futures,无 asyncio.gather)
  - 每页 200 条 NAS 上限,减少请求总数
  - 每次列表请求后 sleep 0.1s(约 10 req/s),减轻 N150 压力
  - 进度每 10s 输出到 stderr,用户可 Ctrl+C

**指纹策略**(经 Step 7.1 验证):
  NAS `/v2/file/list` 返回的字段里 `file_hash` 对真实文件(含 994MB zip)均为空字符串,
  `ext` 字段也常为空 → 用 (size, 从文件名解析的 ext) 作为弱指纹。
  误报高(同名同 size 但内容不同),但只读安全,只用于"候选重复清单"。

命令:
  audit-duplicates [--pool NAME] [--output PATH] [--sample N] [--min-size MB]
  audit-orphans    [--pool NAME] [--output PATH] [--sample N] [--min-size MB]
  audit-all        [--pool NAME] [--output PATH] [--sample N] [--min-size MB]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# 桥接 NAS client(同步接口)
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import nas_client  # noqa: E402


# N150 pacing
SLEEP_BETWEEN_PAGES = 0.1          # 100ms
PROGRESS_INTERVAL = 10.0           # 秒
PAGE_SIZE = 200                    # NAS 单次列表上限
DEFAULT_MIN_SIZE_MB = 1            # 跳过 < 1MB
TOP_DUP_GROUPS_IN_JSON = 200       # 截前 200 组避免输出爆炸
TOP_ORPHANS_IN_JSON = 500

# NAS 错误码
ERR_PERM = "N001411"


# ============ 池发现 ============

def discover_scan_roots(want_pool: str = "") -> list[tuple[str, str, str]]:
    """返回 [(pool_name, root_path, status)],status in {'ok','empty','perm'}。

    NAS `/zspool/info` 的池列表在 `data.pool_list`。每个池的真实用户文件根是
    `/<pool>/my/data/`(不是 `/my/`,`/my/` 直接列会返回 N001411 权限拒绝)。
    """
    roots: list[tuple[str, str, str]] = []
    resp = nas_client.post("/zspool/info", {})
    pools = (resp.get("data") or {}).get("pool_list", []) or []
    for pool in pools:
        name = pool.get("name", "")
        if not name:
            continue
        if want_pool and name != want_pool:
            continue
        root = f"/{name}/my/data/"
        # 探一下根是否可访问
        probe = nas_client.post("/v2/file/list", {
            "folderId": 0, "path": root, "start": 0, "num": 1,
            "sortby": "name", "order": "asc", "show_hidden": 0,
        })
        code = str(probe.get("code", ""))
        if code == ERR_PERM:
            status = "perm"
        elif code != "200":
            status = f"err:{code}"
        else:
            items = (probe.get("data") or {}).get("list", []) or []
            status = "ok" if items else "empty"
        roots.append((name, root, status))
        time.sleep(SLEEP_BETWEEN_PAGES)
    return roots


# ============ DFS 扫描 ============

class ScanState:
    """单次扫描的累积状态。"""
    def __init__(self, args):
        self.args = args
        self.scanned = 0          # 已扫文件数(满足 min_size 的)
        self.skipped_small = 0    # 因 size < min_size 跳过
        self.dirs_visited = 0
        self.errors = []          # (path, code, msg)
        self.last_progress = time.time()
        # 重复指纹分组:fingerprint -> [{"path", "size"}]
        self.groups: dict[str, list[dict]] = {}
        # 孤儿候选:[{"path", "size", "ext", "labels", "ftype"}]
        self.orphans: list[dict] = []
        self.video_hits = 0       # 命中影视源目录的文件数
        self.labeled_hits = 0     # 有标签的文件数

    def progress(self, current_path: str):
        """每 PROGRESS_INTERVAL 秒打一次进度到 stderr。"""
        now = time.time()
        if now - self.last_progress > PROGRESS_INTERVAL:
            print(
                f"[scan] path={current_path} scanned={self.scanned} "
                f"groups={len(self.groups)} orphans={len(self.orphans)} "
                f"dirs={self.dirs_visited}",
                file=sys.stderr,
            )
            self.last_progress = now


def _is_dir(item: dict) -> bool:
    """NAS 用字符串 '1' 表示目录,要做宽松判断。"""
    v = item.get("is_dir")
    return v in (1, "1", True, "true", "True")


def _parse_ext(name: str) -> str:
    """从文件名解析扩展名(小写,无前导点)。NAS 自带的 ext 字段常为空。"""
    if not name or "." not in name:
        return ""
    # 防止 .tar.gz 这类双扩展只取最后一段(够用)
    return name.rsplit(".", 1)[-1].lower()


def _fingerprint(item: dict, name: str) -> str:
    """弱指纹:(size, ext)。

    NAS 的 `file_hash` 对真实文件也返回空字符串(Step 7.1 验证),
    无法用于精确去重 → 退而求其次用 size+ext 分组,组内 >1 即候选重复。
    同 size 同 ext 但内容不同的会误报,需要后续人工核对。
    """
    size = int(item.get("size", 0) or 0)
    ext = _parse_ext(name)
    return f"size:{size}|ext:{ext}"


def dfs_scan(root: str, state: ScanState, video_dirs: list[str]):
    """递归 DFS 扫描一个目录,单线程,每页 sleep。

    `video_dirs` 用于 orphan 检测:文件路径若以其中任一前缀开头 → 算影视相关。
    """
    start = 0
    state.dirs_visited += 1

    while True:
        # sample 限制
        if state.args.sample and state.scanned >= state.args.sample:
            return

        try:
            resp = nas_client.post("/v2/file/list", {
                "folderId": 0, "path": root, "start": start, "num": PAGE_SIZE,
                "sortby": "name", "order": "asc", "show_hidden": 0,
            })
        except Exception as e:
            state.errors.append({"path": root, "code": "EXC", "msg": str(e)[:200]})
            return

        code = str(resp.get("code", ""))
        if code == ERR_PERM:
            state.errors.append({"path": root, "code": code, "msg": resp.get("msg", "")})
            return
        if code != "200":
            state.errors.append({"path": root, "code": code, "msg": resp.get("msg", "")})
            return

        items = (resp.get("data") or {}).get("list", []) or []
        if not items:
            return

        min_bytes = state.args.min_size * 1024 * 1024

        for item in items:
            name = item.get("name", "")
            full_path = root + name

            if _is_dir(item):
                # 进入子目录
                child = full_path + "/"
                dfs_scan(child, state, video_dirs)
                if state.args.sample and state.scanned >= state.args.sample:
                    return
                continue

            # 文件
            size = int(item.get("size", 0) or 0)
            if size < min_bytes:
                state.skipped_small += 1
                continue

            state.scanned += 1

            # 重复指纹分组(所有扫描到的文件都入组,重复与否后处理)
            fp = _fingerprint(item, name)
            state.groups.setdefault(fp, []).append({
                "path": full_path,
                "size": size,
            })

            # orphan 分类
            labels_raw = item.get("labels", "")
            labels = labels_raw.strip() if isinstance(labels_raw, str) else ""
            is_labeled = bool(labels)
            is_video = any(full_path == d or full_path.startswith(d + "/")
                           for d in video_dirs) if video_dirs else False

            if is_video:
                state.video_hits += 1
            if is_labeled:
                state.labeled_hits += 1
            if not is_video and not is_labeled:
                state.orphans.append({
                    "path": full_path,
                    "size": size,
                    "ext": _parse_ext(name),
                    "ftype": item.get("ftype", ""),
                    "labels": labels,
                    "modify_time": item.get("modify_time", ""),
                })

            if state.args.sample and state.scanned >= state.args.sample:
                return

        # 是否还有下一页
        if len(items) < PAGE_SIZE:
            return
        start += PAGE_SIZE
        time.sleep(SLEEP_BETWEEN_PAGES)
        state.progress(root)


# ============ audit-duplicates ============

def cmd_audit_duplicates(args) -> dict:
    """扫所有池,按 (size, ext) 弱指纹找重复。

    返回 JSON 结构:
        cmd, strategy, pools_scanned, total_scanned, total_skipped_small,
        duplicate_groups, total_wasted_bytes, duplicates[200]
    """
    print("[audit-duplicates] 开始,正在发现池...", file=sys.stderr)
    roots = discover_scan_roots(args.pool)
    accessible = [(n, r) for (n, r, s) in roots if s == "ok"]
    skipped = [{"pool": n, "root": r, "status": s} for (n, r, s) in roots if s != "ok"]

    state = ScanState(args)
    for name, root in accessible:
        print(f"[audit-duplicates] 扫描池 {name} ({root})", file=sys.stderr)
        try:
            dfs_scan(root, state, video_dirs=[])
        except KeyboardInterrupt:
            print("[audit-duplicates] 用户中断,输出部分结果", file=sys.stderr)
            state.errors.append({"path": root, "code": "INT", "msg": "KeyboardInterrupt"})
            break

    # 筛出 count > 1 的组,按浪费字节降序
    duplicates = []
    for fp, items in state.groups.items():
        if len(items) < 2:
            continue
        size = items[0]["size"]
        wasted = size * (len(items) - 1)
        duplicates.append({
            "fingerprint": fp,
            "size": size,
            "count": len(items),
            "wasted_bytes": wasted,
            "paths": [it["path"] for it in items],
        })
    duplicates.sort(key=lambda x: x["wasted_bytes"], reverse=True)

    total_wasted = sum(d["wasted_bytes"] for d in duplicates)
    result = {
        "cmd": "audit-duplicates",
        "strategy": "size+ext(weak)",
        "strategy_note": (
            "NAS list_files 的 file_hash 对真实文件也返回空字符串(Step 7.1 验证),"
            "无法用于精确去重。本策略用 (size, ext) 弱指纹分组,组内 >1 即候选重复。"
            "误报率较高(同 size 同 ext 但内容不同),需人工核对。"
        ),
        "pools_scanned": [n for (n, _r) in accessible],
        "pools_skipped": skipped,
        "total_scanned": state.scanned,
        "total_skipped_small": state.skipped_small,
        "dirs_visited": state.dirs_visited,
        "duplicate_groups": len(duplicates),
        "total_wasted_bytes": total_wasted,
        "total_wasted_human": _human_bytes(total_wasted),
        "duplicates": duplicates[:TOP_DUP_GROUPS_IN_JSON],
        "errors": state.errors,
        "truncated": len(duplicates) > TOP_DUP_GROUPS_IN_JSON,
        "truncated_note": (
            f"共 {len(duplicates)} 组重复,JSON 只保留前 {TOP_DUP_GROUPS_IN_JSON} 组"
            if len(duplicates) > TOP_DUP_GROUPS_IN_JSON else ""
        ),
    }
    return result


# ============ audit-orphans ============

def cmd_audit_orphans(args) -> dict:
    """扫所有文件,找无标签 + 不属于任何影视分类源的"野生"文件。

    定义:
      - 在 /zvideo/classification/dirs 关联目录下 → 影视相关,跳过
      - labels 字段非空 → 已打标签,跳过
      - 其余 → 孤儿(候选整理目标)
    """
    print("[audit-orphans] 开始,正在拿影视源目录...", file=sys.stderr)
    dirs_resp = nas_client.post("/zvideo/classification/dirs", {})
    video_dirs = dirs_resp.get("data", []) or []

    print(f"[audit-orphans] 影视源目录 {len(video_dirs)} 个", file=sys.stderr)
    roots = discover_scan_roots(args.pool)
    accessible = [(n, r) for (n, r, s) in roots if s == "ok"]
    skipped = [{"pool": n, "root": r, "status": s} for (n, r, s) in roots if s != "ok"]

    state = ScanState(args)
    for name, root in accessible:
        print(f"[audit-orphans] 扫描池 {name} ({root})", file=sys.stderr)
        try:
            dfs_scan(root, state, video_dirs=video_dirs)
        except KeyboardInterrupt:
            print("[audit-orphans] 用户中断,输出部分结果", file=sys.stderr)
            state.errors.append({"path": root, "code": "INT", "msg": "KeyboardInterrupt"})
            break

    # 按大小降序
    state.orphans.sort(key=lambda x: x["size"], reverse=True)

    total_orphan_bytes = sum(o["size"] for o in state.orphans)
    result = {
        "cmd": "audit-orphans",
        "video_dirs": video_dirs,
        "video_dirs_count": len(video_dirs),
        "pools_scanned": [n for (n, _r) in accessible],
        "pools_skipped": skipped,
        "total_scanned": state.scanned,
        "total_skipped_small": state.skipped_small,
        "dirs_visited": state.dirs_visited,
        "video_hits": state.video_hits,
        "labeled_hits": state.labeled_hits,
        "total_orphans": len(state.orphans),
        "total_orphan_bytes": total_orphan_bytes,
        "total_orphan_human": _human_bytes(total_orphan_bytes),
        "orphans": state.orphans[:TOP_ORPHANS_IN_JSON],
        "errors": state.errors,
        "truncated": len(state.orphans) > TOP_ORPHANS_IN_JSON,
        "truncated_note": (
            f"共 {len(state.orphans)} 个孤儿,JSON 只保留前 {TOP_ORPHANS_IN_JSON} 个"
            if len(state.orphans) > TOP_ORPHANS_IN_JSON else ""
        ),
    }
    return result


# ============ 输出 ============

def _human_bytes(n: int) -> str:
    """把字节数转成人类可读(KB/MB/GB/TB)。"""
    if n is None:
        return "0 B"
    units = [("B", 1), ("KB", 1024), ("MB", 1024**2),
             ("GB", 1024**3), ("TB", 1024**4), ("PB", 1024**5)]
    for unit, factor in reversed(units):
        if n >= factor:
            return f"{n / factor:.2f} {unit}"
    return "0 B"


def _format_duplicates(result: dict) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("file_organizer — 重复文件诊断报告")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"指纹策略: {result['strategy']}")
    lines.append(f"  {result['strategy_note']}")
    lines.append("")
    lines.append(f"扫描池: {', '.join(result['pools_scanned']) or '(无)'}")
    if result["pools_skipped"]:
        for s in result["pools_skipped"]:
            lines.append(f"  ⚠️ 跳过 {s['pool']}: {s['status']}")
    lines.append(f"已扫描文件: {result['total_scanned']}")
    lines.append(f"因 < min_size 跳过: {result['total_skipped_small']}")
    lines.append(f"访问目录数: {result['dirs_visited']}")
    lines.append("")
    lines.append(f"重复组数: {result['duplicate_groups']}")
    lines.append(f"浪费总空间: {result['total_wasted_human']} ({result['total_wasted_bytes']} bytes)")
    if result["errors"]:
        lines.append(f"⚠️ 错误: {len(result['errors'])} 条(见 JSON errors 字段)")
    if result["truncated"]:
        lines.append(f"ℹ️ {result['truncated_note']}")
    lines.append("")

    if not result["duplicates"]:
        lines.append("✓ 没发现候选重复")
        return "\n".join(lines)

    lines.append(f"Top {len(result['duplicates'])} 候选重复组(按浪费空间降序):")
    for i, dup in enumerate(result["duplicates"][:20], 1):
        lines.append(f"  [{i}] size={_human_bytes(dup['size'])} count={dup['count']} "
                     f"wasted={_human_bytes(dup['wasted_bytes'])}")
        lines.append(f"      fp={dup['fingerprint']}")
        for p in dup["paths"][:5]:
            lines.append(f"      - {p}")
        if len(dup["paths"]) > 5:
            lines.append(f"      ... 还有 {len(dup['paths']) - 5} 个")
    if len(result["duplicates"]) > 20:
        lines.append(f"  ... 完整 {len(result['duplicates'])} 组见 JSON")
    return "\n".join(lines)


def _format_orphans(result: dict) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("file_organizer — 孤儿文件诊断报告")
    lines.append("=" * 70)
    lines.append("")
    lines.append("孤儿定义: 既不在影视源目录下,也没打标签的文件")
    lines.append("")
    lines.append(f"扫描池: {', '.join(result['pools_scanned']) or '(无)'}")
    if result["pools_skipped"]:
        for s in result["pools_skipped"]:
            lines.append(f"  ⚠️ 跳过 {s['pool']}: {s['status']}")
    lines.append(f"影视源目录数: {result['video_dirs_count']}")
    lines.append(f"已扫描文件: {result['total_scanned']}")
    lines.append(f"因 < min_size 跳过: {result['total_skipped_small']}")
    lines.append(f"访问目录数: {result['dirs_visited']}")
    lines.append("")
    lines.append(f"命中影视源目录: {result['video_hits']}")
    lines.append(f"已打标签: {result['labeled_hits']}")
    lines.append(f"孤儿文件: {result['total_orphans']}")
    lines.append(f"孤儿总占用: {result['total_orphan_human']} ({result['total_orphan_bytes']} bytes)")
    if result["errors"]:
        lines.append(f"⚠️ 错误: {len(result['errors'])} 条(见 JSON errors 字段)")
    if result["truncated"]:
        lines.append(f"ℹ️ {result['truncated_note']}")
    lines.append("")

    if not result["orphans"]:
        lines.append("✓ 没发现孤儿文件")
        return "\n".join(lines)

    lines.append(f"Top {min(20, len(result['orphans']))} 孤儿(按 size 降序):")
    for i, o in enumerate(result["orphans"][:20], 1):
        lines.append(f"  [{i}] {_human_bytes(o['size'])} {o['ext'] or '?'} "
                     f"ftype={o['ftype']} {o['path']}")
    if len(result["orphans"]) > 20:
        lines.append(f"  ... 完整 {len(result['orphans'])} 个见 JSON")
    return "\n".join(lines)


def _emit(result: dict, args):
    """stdout 输出文本摘要,可选写 JSON 到文件。"""
    if result["cmd"] == "audit-duplicates":
        text = _format_duplicates(result)
    elif result["cmd"] == "audit-orphans":
        text = _format_orphans(result)
    else:
        text = json.dumps(result, ensure_ascii=False, indent=2)

    print(text)
    if args.output:
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"✓ JSON 写入 {args.output}", file=sys.stderr)


# ============ CLI ============

def _add_common(sp):
    sp.add_argument("--pool", default="", help="限定池名,默认扫所有池")
    sp.add_argument("--output", default="", help="写 JSON 到文件")
    sp.add_argument("--sample", type=int, default=0,
                    help="限制扫描文件数(测试用),0 = 不限")
    sp.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE_MB,
                    help=f"忽略小于 N MB 的文件,默认 {DEFAULT_MIN_SIZE_MB}")


def main():
    p = argparse.ArgumentParser(
        description="NAS 文件诊断(只读,不动 NAS)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd_name in ("audit-duplicates", "audit-orphans", "audit-all"):
        sp = sub.add_parser(cmd_name, help=f"运行 {cmd_name}")
        _add_common(sp)

    args = p.parse_args()
    exit_code = 0

    try:
        if args.cmd in ("audit-duplicates", "audit-all"):
            result = cmd_audit_duplicates(args)
            _emit(result, args)
        if args.cmd in ("audit-orphans", "audit-all"):
            result = cmd_audit_orphans(args)
            _emit(result, args)
    except KeyboardInterrupt:
        print("\n[main] 用户中断(Ctrl+C),退出码 130", file=sys.stderr)
        exit_code = 130
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[main] 异常: {type(e).__name__}: {e}", file=sys.stderr)
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
