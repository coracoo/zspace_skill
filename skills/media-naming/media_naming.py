#!/usr/bin/env python3
"""media-naming skill 命令行入口

设计原则:
- 只读扫描走脚本(正向合规验证)
- 写操作走 MCP tool(rename / move / mkdir / remove) — 本脚本不提供 apply 子命令
- N150 限速:串行不并发,每步 sleep 0.1s
- 复用顶层 nas.NasClient

用法:
  python media_naming.py scan --root /sata14/my/data/影视
  python media_naming.py scan --root ... --json --output /tmp/issues.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.nas_client import NasClient, _load_env  # noqa: E402

MAX_DEPTH = 8
PAGE_SIZE = 50
RATE_SLEEP = 0.1

VIDEO_EXTS = {"mp4", "mkv", "avi", "ts", "rmvb", "flv", "wmv", "mov", "iso", "m2ts"}
SUB_EXTS = {"srt", "ass", "ssa", "sub", "idx"}
JUNK_EXTS = {"torrent", "nfo", "td", "htm", "html", "url", "txt", "jpg", "png", "nzb"}

# ── 合规格式 ──────────────────────────────────────────────

MOVIE_DIR_OK = re.compile(
    r"^[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\d·A-Za-z]+"
    r"\s+"
    r"[\w][\w\s\':,.\-&!()0-9]+"
    r"(\s*\(\d{4}\))?"
    r"(\s*\[.*\])?"
    r"(\s*(1-\d|\d-\d|CD\d|导演剪辑版|\[副本\d?\]))?"
    r"$"
)

SERIES_DIR_OK = re.compile(
    r"^[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\d·A-Za-z]+"
    r"\s+"
    r"[\w][\w\s\':,.\-&!()0-9]+"
    r"(\s*S\d{2}(-S\d{2})?)?"
    r"(\s*\(\d{4}\))?"
    r"(\s*(特别篇|\d))?"
    r"$"
)

SERIES_FILE_OK = re.compile(
    r"^"
    r"([\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\d·A-Za-z]+\s+[\w][\w\s\':,.\-&!()0-9]+\s+)?"
    r"("
    r"E\d{2,3}"
    r"|S\d{2}\s*E\d{2,3}"
    r"|E\d{2,3}-E\d{2,3}"
    r"|S\d{2}\s*E\d{2,3}-E\d{2,3}"
    r")"
    r"(\s*(END|V\d))?"
    r"(\s*\[[\w.\s]+\])?"
    r"\s*\."
    r"(mp4|mkv|avi|ts|rmvb|flv|wmv|mov)$",
    re.I,
)

SERIES_SPECIAL_OK = re.compile(
    r"^"
    r"([\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\d·A-Za-z]+\s+[\w][\w\s\':,.\-&!()0-9]+\s+)?"
    r"(SP\d{2}(\s+[\u4e00-\u9fffA-Za-z]+)?"
    r"|花絮|特辑|彩蛋|预告|番外|幕后|特别篇|精华版|前传"
    r"|[\u4e00-\u9fff][\u4e00-\u9fff\w\s]*)"
    r"\.(mp4|mkv|avi|ts)$"
)

BLACKLIST_CHARS = re.compile(r"[丨｜]")
LETTER_SUB = re.compile(
    r"(?:^[A-Z]{1,2}[\u4e00-\u9fff])"
    r"|(?:[\u4e00-\u9fff][A-Z]{1,3}[\u4e00-\u9fff])"
    r"|(?:[\u4e00-\u9fff][A-Z]{1,3}$)"
)
WATERMARK = re.compile(
    r"【|】|\[微信|\[公众号|￡|@圣城|Mp4Ba|XZYS|XunLeiJia|"
    r"kkkanba|字幕侠|霸王龙|压制组|微信|爱影哥|瞎看菌|雷锋菌|影喵儿|"
    r"情话菌|影视步行街|RARBG|STUTTERSHIT|SmY|CHAOSPACE",
    re.I,
)
PLACEHOLDER_ENGLISH = re.compile(
    r"\s+Erta\s*$|\s+TBD\s*$|\s+Unknown\s*$|\s+XXX\s*$",
    re.I,
)


def movie_file_ok(filename: str, folder_name: str) -> bool:
    stem = filename.rsplit(".", 1)[0]
    ext = filename.rsplit(".", 1)[-1].lower()
    if stem == folder_name:
        return True
    if stem.startswith(folder_name):
        suffix = stem[len(folder_name) :]
        if re.match(
            r"^(\s*(CD\d|_\d|\[\w+\]|E\d{2,3}|\[粤语\]|\[国语\]|\[v\d\]|前传\d?))*$",
            suffix,
        ):
            return True
    if re.search(r"\d+-\d+$", folder_name):
        return True
    if ext in SUB_EXTS and stem.startswith(folder_name):
        return True
    return False


def validate(item: dict, root: str) -> list[str]:
    """返回问题列表。空列表=合规。"""
    path = item["path"]
    name = item["name"]
    is_dir = item["is_dir"]
    problems: list[str] = []

    rel = path.replace(root.rstrip("/") + "/", "") if path.startswith(root.rstrip("/")) else path
    ext = name.rsplit(".", 1)[-1].lower() if "." in name and not is_dir else ""
    stem = name.rsplit(".", 1)[0] if ext else name

    in_movie = "/电影/" in path or path.rstrip("/").endswith("/电影")
    in_series = "/剧集/" in path or path.rstrip("/").endswith("/剧集")
    if not in_movie and not in_series:
        return []

    if BLACKLIST_CHARS.search(name):
        problems.append("审查规避字符(丨｜)")
    if WATERMARK.search(name):
        problems.append("水印/站点标签")

    clean_stem = re.sub(r"\[.*?\]|\(.*?\)", "", stem)
    if LETTER_SUB.search(clean_stem):
        if not re.match(r"^[ES]\d", name):
            if not re.match(r"^(CD|4K|3D|2D|TV|HD|MP|ID)\d*", clean_stem):
                if not re.search(r"[a-z][A-Z]", clean_stem):
                    problems.append("疑似字母替代汉字")

    if is_dir and PLACEHOLDER_ENGLISH.search(name):
        problems.append("占位符英文名(需查找正确英文名)")

    if not is_dir and ext in JUNK_EXTS:
        problems.append("垃圾文件")
        return problems
    if not is_dir and name.endswith(".bt.td"):
        problems.append("下载残留")
        return problems

    root_n = root.rstrip("/")
    if in_movie:
        if is_dir and path.rstrip("/") == f"{root_n}/电影/{name}":
            if not MOVIE_DIR_OK.match(name):
                problems.append("电影文件夹名不合规")
            if re.search(r"\d+-\d+$", name):
                problems.append("合集文件夹(应拆分为独立文件夹)")

        if is_dir and re.match(r"^花絮(\s*-\s*.+)?$", name):
            return []

        if not is_dir and ext in VIDEO_EXTS:
            parts = rel.split("/")
            if any(re.match(r"^花絮", p) for p in parts[1:]):
                return []
            if len(parts) >= 3:
                folder = parts[1]
                if not movie_file_ok(name, folder):
                    problems.append("电影视频文件名不匹配文件夹")

        if not is_dir and path.rstrip("/") == f"{root_n}/电影/{name}":
            if ext in VIDEO_EXTS:
                problems.append("电影散文件(应放入独立文件夹)")

    if in_series:
        if is_dir and path.rstrip("/") == f"{root_n}/剧集/{name}":
            if not SERIES_DIR_OK.match(name):
                problems.append("剧集文件夹名不合规")

        if not is_dir and ext in VIDEO_EXTS:
            parts = rel.split("/")
            if len(parts) >= 3:
                if not SERIES_FILE_OK.match(name) and not SERIES_SPECIAL_OK.match(name):
                    problems.append("剧集视频文件名不合规")

    if not is_dir and ext in (VIDEO_EXTS | SUB_EXTS):
        if re.match(r"^[A-Za-z][\w.]+\.\d{4}\.", name):
            problems.append("PT/Scene原始命名")

    if re.search(r"\.qsv\.|\.flv\.mp4$", name):
        problems.append("格式转换残留")

    return problems


class MediaNaming:
    def __init__(self) -> None:
        try:
            _load_env()
        except SystemExit:
            raise
        self.nas = NasClient()

    async def _list_page(self, path: str, start: int) -> list[dict]:
        # API.md: /v2/file/list 用 start + num
        r = await self.nas.post(
            "/v2/file/list",
            {"path": path, "start": start, "num": PAGE_SIZE, "show_hidden": 0},
        )
        if str(r.get("code")) != "200":
            print(
                f"⚠️ list {path!r} code={r.get('code')} msg={r.get('msg', '')}",
                file=sys.stderr,
            )
            return []
        return r.get("data", {}).get("list", []) or []

    async def scan_all(self, path: str, depth: int = 0):
        """递归遍历(处理分页)。yield {path,name,is_dir,depth}。"""
        if depth > MAX_DEPTH:
            return
        start = 0
        while True:
            try:
                items = await self._list_page(path, start)
            except Exception as e:
                print(f"⚠️ list {path!r} 失败: {e}", file=sys.stderr)
                break
            if not items:
                break
            for item in items:
                name = item.get("name", "")
                item_path = item.get("path") or f"{path.rstrip('/')}/{name}"
                is_dir = str(item.get("is_dir", "0")) == "1"
                yield {
                    "path": item_path,
                    "name": name,
                    "is_dir": is_dir,
                    "depth": depth,
                }
                if is_dir:
                    async for child in self.scan_all(item_path, depth + 1):
                        yield child
            if len(items) < PAGE_SIZE:
                break
            start += PAGE_SIZE
            await asyncio.sleep(RATE_SLEEP)

    async def scan(self, root: str) -> dict:
        root = root.rstrip("/")
        print(f"正在扫描 {root} ...\n", file=sys.stderr)

        stats = {"dirs": 0, "files": 0}
        issues: list[dict] = []
        top_dirs: dict[str, list[str]] = {}

        async for item in self.scan_all(root):
            if item["is_dir"]:
                stats["dirs"] += 1
                # 一级子目录: root/电影|剧集/Name
                if item["path"].count("/") == root.count("/") + 2:
                    name = item["name"]
                    base = re.sub(r"\s*\[.*?\]", "", name)
                    base = re.sub(r"\s*\(副本\d?\)", "", base)
                    top_dirs.setdefault(base, []).append(name)
            else:
                stats["files"] += 1

            problems = validate(item, root)
            if problems:
                rel = item["path"].replace(root + "/", "")
                issues.append(
                    {
                        "path": rel,
                        "name": item["name"],
                        "is_dir": item["is_dir"],
                        "problems": problems,
                    }
                )

        for base, names in top_dirs.items():
            if len(names) > 1:
                for n in names:
                    issues.append(
                        {
                            "path": n,
                            "name": n,
                            "is_dir": True,
                            "problems": [f"疑似重复资源({len(names)}个)"],
                        }
                    )

        print(
            f'扫描完成: {stats["dirs"]} 目录, {stats["files"]} 文件\n',
            file=sys.stderr,
        )
        return {
            "root": root,
            "stats": stats,
            "count": len(issues),
            "issues": issues,
        }


def _print_human(result: dict) -> None:
    issues = result["issues"]
    if not issues:
        print("✅ 全部合规，零问题！")
        return

    by_type: dict[str, list] = {}
    for issue in issues:
        for p in issue["problems"]:
            by_type.setdefault(p, []).append(issue)

    print(f"⚠  发现 {len(issues)} 个问题项:\n")
    for ptype, items in sorted(by_type.items(), key=lambda x: -len(x[1])):
        print(f"【{ptype}】{len(items)} 项")
        for item in items[:8]:
            tag = "📁" if item["is_dir"] else "  "
            print(f"  {tag} {item['path']}")
        if len(items) > 8:
            print(f"  ... 还有 {len(items) - 8} 项")
        print()


async def _async_main(args: argparse.Namespace) -> int:
    mgr = MediaNaming()
    if args.cmd == "scan":
        result = await mgr.scan(args.root)
        if args.output:
            Path(args.output).write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"已写入 {args.output}", file=sys.stderr)
        if args.json:
            json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            _print_human(result)
        return 0
    print(f"未知命令: {args.cmd}", file=sys.stderr)
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="media-naming: 影视文件命名正向校验")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="正向合规扫描(只读)")
    scan_p.add_argument(
        "--root",
        required=True,
        help="影视根目录,如 /sata14/my/data/影视 (下含 电影/ 剧集/)",
    )
    scan_p.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    scan_p.add_argument("--output", help="写入 JSON 文件路径")

    args = parser.parse_args()
    raise SystemExit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
