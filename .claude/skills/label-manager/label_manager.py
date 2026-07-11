#!/usr/bin/env python3
"""label-manager skill 命令行入口

设计原则:
- 只读操作走脚本(扫目录、反向查标签、列标签)
- 写操作走 MCP tool(让 LLM 显式确认) — 所以这里不提供 apply / delete 子命令
- N150 限速:串行不并发,每步 sleep 0.1s,扫目录 max-depth 默认 5
- 复用 mcp_server.py 的 NasClient,不重复实现登录

用法:
  python label_manager.py list-labels
  python label_manager.py scan --root /sata14/my/data/ --ext yml --max-depth 5 --output /tmp/scan.json
  python label_manager.py find-by-label --label docker --output /tmp/docker.json
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# 让 `from lib.nas_client import ...` 能解析
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.nas_client import NasClient, PROJECT_ROOT, _load_env  # noqa: E402


def _ensure_env():
    """env 已经在 import lib.nas_client 时加载过了(它要 import mcp_server,必须先有 env)。
    这里再调一次是兜底 + 给清晰的错误信息。"""
    try:
        _load_env()
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"❌ 加载 .env 失败: {e}")


class LabelManager:
    """组合现有 MCP tool 的"批量活"封装。"""

    def __init__(self):
        _ensure_env()
        self.nas = NasClient()  # 登录由第一次 post/get 自动触发

    async def list_labels(self) -> dict:
        """列出所有标签。"""
        r = await self.nas.post("/v2/labels/alllabels", {})
        return {
            "ok": str(r.get("code")) == "200",
            "labels": r.get("data", {}).get("list", []),
            "raw_code": r.get("code"),
            "raw_msg": r.get("msg", ""),
        }

    async def scan_directory(
        self,
        root: str,
        ext: list[str] | None = None,
        max_depth: int = 5,
    ) -> dict:
        """BFS 扫描 root 下所有文件(NAS 没 find -r,只能 list_files 逐层)。

        ext: 只返回匹配的扩展名(如 ["yml", "yaml"]);None = 不过滤
        max_depth: 限制递归深度,防 N150 卡死

        NAS 字段类型注意:
          - is_dir 是字符串 "0"/"1"(不是 bool)
          - size / modify_time 是字符串(不是 int)
          - data.list 是文件列表(NAS 内部字段叫 list 不是 items)
          - labels 字段是逗号分隔字符串,如 "docker,重要"
        """
        # 路径规范化:目录必须以 / 结尾
        root = root.rstrip("/") + "/"
        if ext:
            ext = [e.lower().lstrip(".") for e in ext]

        items: list[dict] = []
        dirs_to_visit: list[tuple[str, int]] = [(root, 0)]
        seen_dirs: set[str] = {root}
        scanned_dirs = 0
        rate_sleep = 0.1

        while dirs_to_visit:
            current, depth = dirs_to_visit.pop(0)
            if depth > max_depth:
                continue
            scanned_dirs += 1
            if scanned_dirs % 20 == 1:
                print(f"  ⏳ 已扫 {scanned_dirs} 个目录(队列剩 {len(dirs_to_visit)} 个)", file=sys.stderr)
            try:
                r = await self.nas.post("/v2/file/list", {"path": current, "start": 0, "num": 200})
            except Exception as e:
                print(f"⚠️ list {current!r} 失败: {e}", file=sys.stderr)
                continue
            if str(r.get("code")) != "200":
                print(f"⚠️ list {current!r} code={r.get('code')} msg={r.get('msg','')}", file=sys.stderr)
                continue

            for item in r.get("data", {}).get("list", []):
                name = item.get("name", "")
                is_dir = item.get("is_dir") == "1"
                if is_dir:
                    if depth < max_depth:
                        sub_path = item.get("path") or (current + name + "/")
                        if not sub_path.endswith("/"):
                            sub_path += "/"
                        if sub_path not in seen_dirs:
                            seen_dirs.add(sub_path)
                            dirs_to_visit.append((sub_path, depth + 1))
                else:
                    if ext:
                        file_ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                        if file_ext not in ext:
                            continue
                    labels_str = item.get("labels", "") or ""
                    items.append({
                        "path": item.get("path", ""),
                        "name": name,
                        "size": int(item.get("size", 0) or 0),
                        "modify_time": int(item.get("modify_time", 0) or 0),
                        "is_dir": False,
                        "labels": [s.strip() for s in labels_str.split(",") if s.strip()],
                    })

            await asyncio.sleep(rate_sleep)

        return {
            "root": root,
            "ext_filter": ext,
            "max_depth": max_depth,
            "scanned_dirs": scanned_dirs,
            "count": len(items),
            "items": items,
        }

    async def find_files_by_label(
        self,
        label: str,
        root: str = "/sata14/my/",
        max_depth: int = 5,
    ) -> dict:
        """反向查询:BFS 遍历 root 下所有文件,匹配指定标签。

        实测发现:NAS 的 /v2/file/list 返回的每个 item 都带 `labels` 字段
        (逗号分隔字符串),所以可以直接扫目录拿,不需要走 recent_files + file_info。
        这比 recent_files(992 项硬上限)更完整 — 只要目录在 BFS 范围内就能找到。

        ⚠️ 已知 gap:
          - 严格受 max_depth 限制,深度外的文件找不到
          - 用户只能扫自己 /<pool>/my/<子目录>/,跨池越权 N001411
          - 实测 is_dir 是字符串 "0"/"1",labels 是逗号分隔字符串
        """
        root = root.rstrip("/") + "/"
        matches: list[dict] = []
        scanned_dirs = 0
        scanned_files = 0
        dirs_to_visit: list[tuple[str, int]] = [(root, 0)]
        seen_dirs: set[str] = {root}
        rate_sleep = 0.1

        while dirs_to_visit:
            current, depth = dirs_to_visit.pop(0)
            if depth > max_depth:
                continue
            scanned_dirs += 1
            if scanned_dirs % 20 == 1:
                print(f"  ⏳ 已扫 {scanned_dirs} 个目录(队列剩 {len(dirs_to_visit)} 个)", file=sys.stderr)
            try:
                r = await self.nas.post("/v2/file/list", {"path": current, "start": 0, "num": 200})
            except Exception as e:
                print(f"⚠️ list {current!r} 失败: {e}", file=sys.stderr)
                continue
            if str(r.get("code")) != "200":
                print(f"⚠️ list {current!r} code={r.get('code')} msg={r.get('msg','')}", file=sys.stderr)
                continue

            for item in r.get("data", {}).get("list", []):
                name = item.get("name", "")
                is_dir = item.get("is_dir") == "1"
                labels_str = item.get("labels", "") or ""
                file_labels = [s.strip() for s in labels_str.split(",") if s.strip()]

                if is_dir:
                    if depth < max_depth:
                        sub_path = item.get("path") or (current + name + "/")
                        if not sub_path.endswith("/"):
                            sub_path += "/"
                        if sub_path not in seen_dirs:
                            seen_dirs.add(sub_path)
                            dirs_to_visit.append((sub_path, depth + 1))
                    # 目录本身也算"打标签的对象",如果目录打了标签也算匹配
                    if label in file_labels:
                        matches.append({
                            "path": item.get("path", ""),
                            "name": name,
                            "is_dir": True,
                            "labels": file_labels,
                        })
                else:
                    scanned_files += 1
                    if label in file_labels:
                        matches.append({
                            "path": item.get("path", ""),
                            "name": name,
                            "is_dir": False,
                            "size": int(item.get("size", 0) or 0),
                            "labels": file_labels,
                        })

            await asyncio.sleep(rate_sleep)

        return {
            "ok": True,
            "label": label,
            "root": root,
            "max_depth": max_depth,
            "scanned_dirs": scanned_dirs,
            "scanned_files": scanned_files,
            "matched": len(matches),
            "matches": matches,
        }


# ============ CLI ============

def _print_or_save(data: dict, output: str | None):
    if output:
        Path(output).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✓ 已写入 {output}", file=sys.stderr)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


async def _async_main(args):
    mgr = LabelManager()
    if args.cmd == "list-labels":
        result = await mgr.list_labels()
    elif args.cmd == "scan":
        ext_list = [e.strip() for e in args.ext.split(",")] if args.ext else None
        result = await mgr.scan_directory(args.root, ext_list, args.max_depth)
    elif args.cmd == "find-by-label":
        result = await mgr.find_files_by_label(args.label, args.root, args.max_depth)
    else:
        raise SystemExit(f"unknown cmd: {args.cmd}")
    await mgr.nas.aclose()
    return result


def main():
    parser = argparse.ArgumentParser(description="label-manager skill 命令行入口")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-labels", help="列出所有 NAS 标签")

    s = sub.add_parser("scan", help="BFS 扫描目录找文件")
    s.add_argument("--root", required=True, help="根目录路径,如 /sata14/my/data/")
    s.add_argument("--ext", default=None, help="扩展名过滤,逗号分隔,如 yml,yaml")
    s.add_argument("--max-depth", type=int, default=5, help="最大递归深度(默认 5)")
    s.add_argument("--output", default=None, help="结果写入 JSON 文件,默认 stdout")

    f = sub.add_parser("find-by-label", help="按标签反向查找文件(BFS 扫目录匹配 labels 字段)")
    f.add_argument("--label", required=True, help="标签名,如 docker")
    f.add_argument("--root", default="/sata14/my/data/", help="扫描根目录,默认 /sata14/my/data/")
    f.add_argument("--max-depth", type=int, default=5, help="最大递归深度(默认 5)")
    f.add_argument("--output", default=None, help="结果写入 JSON 文件,默认 stdout")

    args = parser.parse_args()
    result = asyncio.run(_async_main(args))
    _print_or_save(result, getattr(args, "output", None))


if __name__ == "__main__":
    main()