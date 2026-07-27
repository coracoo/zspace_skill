#!/usr/bin/env python3
"""media-organizer skill 命令行入口 — NAS 极影视诊断

⚠️ **只读诊断,不动 NAS** — 这是纯审计工具,所有"修复"建议只在报告里。

为什么只读:
  - 合并/移动分类会触发 NAS 重新扫描(task),可能跑几十分钟
  - 诊断先看问题,修复用 MCP tool + LLM 二次确认
  - migrate 例外:物理文件挪动走 API,但默认 dry-run,逐条确认

诊断命令:
  audit-classifications  审计分类(重名 / 空 / 异常名)
  audit-sources         审计源目录(不该在影视库的路径)
  audit-collections     抽样审计 collection(type 分布 + 分类一致性)
  audit-all             一键全跑,输出综合报告
  migrate               按规则迁移错放文件(默认 dry-run)
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.migration_rules import (  # noqa: E402
    MigrationConfig,
    load_config,
    match_rule,
)
from lib.nas_client import NasClient, PROJECT_ROOT  # noqa: E402


# NAS type 字段语义(从随机采样推断,常见值)
TYPE_LABELS = {
    100: "电影",
    200: "电视剧",
    300: "综艺/纪录片/其他",
}

# 系统目标分类:type → 可能的名字(系统分类用,用户自建的同名不算)
SYSTEM_NAME_BY_TYPE = {
    100: ["电影", "Movie", "movie"],
    200: ["电视剧", "剧集", "TV", "tv", "TV Series"],
    300: ["综艺", "纪录片", "Variety", "variety", "Documentary", "documentary"],
}


class MediaAuditor:
    """极影视只读诊断器。"""

    def __init__(self):
        self.nas = NasClient()

    async def audit_classifications(self) -> dict:
        """分类审计。

        检测:
          - 重名分类(is_system=0 的跟系统分类同名,典型的"用户重复建")
          - 空分类(series_count=0 + collection_count=0)
          - 异常名(全英文 / 含路径分隔符 / 长度异常)
          - 没启用的(is_enable=0)
        """
        r = await self.nas.post("/zvideo/classification/list", {})
        if str(r.get("code")) != "200":
            return {"ok": False, "error": f"code={r.get('code')} msg={r.get('msg')}"}
        all_classes = r.get("data", [])

        # 按 name 分组查重名
        by_name: dict[str, list[dict]] = {}
        for c in all_classes:
            by_name.setdefault(c.get("name", ""), []).append(c)

        duplicate_names = {
            n: items for n, items in by_name.items() if len(items) > 1
        }

        system_names = {c.get("name") for c in all_classes if c.get("is_system") == 1}
        user_classes = [c for c in all_classes if c.get("is_system") == 0]
        user_clashing_with_system = [
            c for c in user_classes if c.get("name") in system_names
        ]

        empty_classes = [
            c for c in all_classes
            if c.get("series_count", 0) == 0 and c.get("collection_count", 0) == 0
        ]

        # 系统分类被关闭(is_system=1 + is_enable=0)
        # 这是独立 finding:用户主动关了 NAS 内置的"电影/电视剧"分类,
        # 所以同名用户分类才有 coll —— 这是同名歧义的根因。
        disabled_system_classes = [
            c for c in all_classes
            if c.get("is_system") == 1 and c.get("is_enable") == 0
        ]

        # 异常名检测:全英文(可能是临时调试名如 frds/test)/ 含分隔符 / 长度异常
        unusual_names = []
        for c in all_classes:
            n = c.get("name", "")
            reasons = []
            if re.match(r"^[a-zA-Z]+$", n) and len(n) <= 8:
                reasons.append("全英文短名(疑似临时/调试名)")
            if "/" in n or "\\" in n:
                reasons.append("含路径分隔符")
            if len(n) > 20:
                reasons.append(f"名字过长({len(n)} 字)")
            if c.get("is_enable") == 0 and c.get("is_system") == 0:
                reasons.append("用户分类未启用(is_enable=0)")
            if reasons:
                unusual_names.append({"class": c, "reasons": reasons})

        return {
            "ok": True,
            "total": len(all_classes),
            "system_count": sum(1 for c in all_classes if c.get("is_system") == 1),
            "user_count": len(user_classes),
            "duplicates": duplicate_names,
            "user_clashing_with_system": user_clashing_with_system,
            "empty_classes": empty_classes,
            "disabled_system_classes": disabled_system_classes,
            "unusual_names": unusual_names,
            "all_classes": all_classes,
        }

    async def audit_sources(self) -> dict:
        """源目录审计。

        检测:
          - 不该在影视库的路径(用户的 /my/data/<非影视目录>)
          - 网络挂载路径(/zspace/extdev/*)— SMB/CIFS 共享,源在另一台机器
            (实测 NAS 这边 API 可写,移动跨挂载点由 NAS 后端处理)
          - 重叠扫描(两个分类扫同一目录 — 间接证据)
        """
        r = await self.nas.post("/zvideo/classification/dirs", {})
        if str(r.get("code")) != "200":
            return {"ok": False, "error": f"code={r.get('code')} msg={r.get('msg')}"}
        dirs = r.get("data", [])

        user_pool_dirs = []  # 在用户 /sata14/my/data/ 下的
        network_mounts = []  # 在 /zspace/extdev/ 下的(CIFS 网络挂载,源在 192.168.x.x)
        suspicious = []  # 可疑:用户池里但不像影视目录

        for raw in dirs:
            # NAS 可能返回字符串或 dict(含 path/dir 字段);统一归一化成字符串
            d = raw if isinstance(raw, str) else (raw.get("path") or raw.get("dir") or "") if isinstance(raw, dict) else ""
            if not d:
                continue
            entry = {"path": d}
            if d.startswith("/sata14/my/"):
                user_pool_dirs.append(entry)
                # 检测:用户的非 my/data/ 子路径,或者在 my/data/ 但不是影视子目录
                if d.startswith("/sata14/my/data/"):
                    tail = d.replace("/sata14/my/data/", "", 1).strip("/")
                    top = tail.split("/", 1)[0] if tail else ""
                    non_media = {"music", "备份", "backup", "文档", "docs", "photo", "照片"}
                    if top.lower() in non_media or any(kw in d.lower() for kw in ["备份", "music", "document", "文档"]):
                        suspicious.append({**entry, "reason": f"在用户 /my/data/ 但不是影视目录(top={top!r})"})
                else:
                    suspicious.append({**entry, "reason": "在 /sata14/my/ 下但不在 /my/data/ 子目录"})
            elif d.startswith("/zspace/extdev/"):
                # 实测:这是 CIFS 网络挂载,源在 192.168.x.x(另一台机器)。
                # NAS 这边 API 可写(uid 匹配挂载 owner),但不一定是真"本地"路径。
                network_mounts.append(entry)
            else:
                suspicious.append({**entry, "reason": f"未知前缀: {d[:30]}"})

        return {
            "ok": True,
            "total": len(dirs),
            "user_pool_dirs": user_pool_dirs,
            "network_mounts": network_mounts,
            "suspicious": suspicious,
            "all_dirs": dirs,
        }

    async def audit_collections(self, sample_count: int = 8) -> dict:
        """影片抽样审计。

        由于 NAS 没"按分类列 collection"的全量端点(series/list count=0),
        只能通过 randomlist 多采样覆盖更多 collection。
        sample_count 次 randomlist,每次 12 部,理论最多 12*sample_count 部(去重后更少)。

        检测:
          - 分类名与 type 不匹配(比如分类叫 frds 实际是电影)
          - type 分布(type=100 电影、200 电视剧 等的比例)
          - 唯一分类里的 type 多样性(高分 = 分类不规范)
        """
        seen: dict[str, dict] = {}
        calls_made = 0

        for _ in range(sample_count):
            r = await self.nas.post("/zvideo/video/randomlist", {})
            calls_made += 1
            lst = r.get("data", [])
            if not isinstance(lst, list):
                continue
            for item in lst:
                cid = item.get("collection_id", "")
                if not cid or cid in seen:
                    continue
                seen[cid] = item

        collections = list(seen.values())

        # 按分类聚合 type 分布
        by_class: dict[str, dict[int, int]] = {}
        type_counter: dict[int, int] = {}
        for c in collections:
            cn = c.get("classification_name", "?")
            t = c.get("type", 0)
            by_class.setdefault(cn, {}).setdefault(t, 0)
            by_class[cn][t] += 1
            type_counter[t] = type_counter.get(t, 0) + 1

        # 检测:分类名与 type 不一致
        # 启发式:分类名带"剧"字的应该都是 type=200;
        # 分类名带"影"字的应该都是 type=100;
        # 分类名含"动画/动漫"应该都是 type=300(实测)
        mismatches = []
        for cn, type_dist in by_class.items():
            cn_lower = cn.lower()
            issues = []
            if "剧" in cn or "series" in cn_lower:
                if type_dist.get(100, 0) > 0 and type_dist.get(200, 0) == 0:
                    issues.append(f"分类名 {cn!r} 含'剧'字,但抽样里 {type_dist[100]} 部都是 type=100(电影)")
            elif "影" in cn or "movie" in cn_lower:
                if type_dist.get(200, 0) > 0 and type_dist.get(100, 0) == 0:
                    issues.append(f"分类名 {cn!r} 含'影'字,但抽样里 {type_dist[200]} 部都是 type=200(电视剧)")
            # 异常名 + 多类型混杂
            if re.match(r"^[a-zA-Z]+$", cn) and len(cn) <= 8 and len(type_dist) > 1:
                issues.append(f"分类名 {cn!r} 像英文缩写/临时名,且抽到 {len(type_dist)} 种 type")
            if issues:
                mismatches.append({"classification": cn, "issues": issues, "type_dist": type_dist})

        return {
            "ok": True,
            "calls_made": calls_made,
            "unique_collections": len(collections),
            "type_distribution": type_counter,
            "type_labels": TYPE_LABELS,
            "by_classification": by_class,
            "mismatches": mismatches,
            "samples": collections[:30],
        }

    async def audit_all(self, sample_count: int = 8, suggest_sample: int = 20) -> dict:
        """一键全跑,生成综合报告。"""
        cls = await self.audit_classifications()
        src = await self.audit_sources()
        col = await self.audit_collections(sample_count)
        moves = await self.suggest_moves(suggest_sample)
        return {"classifications": cls, "sources": src, "collections": col, "moves": moves}

    async def suggest_moves(self, sample_count: int = 20) -> dict:
        """Per-collection 挪分类建议。

        思路:
          1. 找异常分类(全英文短名 / 含路径分隔符 / 长度异常 / 未启用)
          2. 找系统目标分类(type → name 映射)
          3. 抽样 randomlist N 次,对每个 collection:
             - 当前分类是异常的?
             - 它的 type 有对应系统目标?
             - 已经在对的分类?(跳过)
          4. 列出"应该挪到 X 分类"的 collection 列表

        ⚠️ NAS 没暴露"挪 collection 分类"的 API 端点(10 个候选路径都 403),
        本命令只生成建议,执行需要 pcweb UI 或改源目录 + classification/rescan。
        """
        # 1. 异常分类(复用 audit_classifications 的 unusual_names)
        cls_audit = await self.audit_classifications()
        if not cls_audit.get("ok"):
            return {"ok": False, "error": cls_audit.get("error")}

        abnormal_cls_ids: set[str] = set()
        for u in cls_audit.get("unusual_names", []):
            abnormal_cls_ids.add(u["class"].get("id"))

        # 2. 找同名分类候选 + 选最优目标
        # 启发式:同名分类里,优先选 is_system=0 但 collection_count>0 的(用户实际在用的)
        # 退而求其次:is_system=1(系统同名)
        # 再不行:有内容的任意同名
        cls_resp = await self.nas.post("/zvideo/classification/list", {})
        if str(cls_resp.get("code")) != "200":
            return {"ok": False, "error": f"code={cls_resp.get('code')} msg={cls_resp.get('msg')}"}
        all_cls = cls_resp.get("data", [])

        def _pick_best(candidates: list[dict]) -> tuple[dict | None, list[dict]]:
            """从同名候选里挑最优目标,返回(选中, 其他备选)。"""
            if not candidates:
                return (None, [])
            # 评分:is_system=0 优先(score 0),is_system=1 次之(score 10)
            # 同分按 collection_count 降序
            scored = sorted(
                candidates,
                key=lambda c: (
                    10 if c.get("is_system") == 1 else 0,
                    -c.get("collection_count", 0),
                ),
            )
            return (scored[0], scored[1:])

        system_targets: dict[int, dict] = {}
        target_alternatives: dict[int, list[dict]] = {}
        target_disabled: dict[int, list[dict]] = {}
        for t, names in SYSTEM_NAME_BY_TYPE.items():
            candidates = [c for c in all_cls if c.get("name") in names]
            # 先按 is_enable=0 过滤(系统关闭的分类永远不当目标)
            enabled = [c for c in candidates if c.get("is_enable") != 0]
            disabled = [c for c in candidates if c.get("is_enable") == 0]
            target_disabled[t] = [
                {"id": d.get("id"), "name": d.get("name"),
                 "is_system": d.get("is_system"),
                 "collection_count": d.get("collection_count", 0)}
                for d in disabled
            ]
            best, alts = _pick_best(enabled)
            if best:
                system_targets[t] = {"id": best.get("id"), "name": best.get("name")}
                target_alternatives[t] = alts

        if not system_targets:
            return {
                "ok": False,
                "error": "没找到任何系统目标分类(可能 type 字段语义与已知不符,看 audit-collections 的 --output JSON)",
            }

        # 3. 抽样
        seen: dict[str, dict] = {}
        for _ in range(sample_count):
            r = await self.nas.post("/zvideo/video/randomlist", {})
            lst = r.get("data", [])
            if not isinstance(lst, list):
                continue
            for item in lst:
                cid = item.get("collection_id", "")
                if cid and cid not in seen:
                    seen[cid] = item

        # 4. 找建议
        suggestions: list[dict] = []
        per_cls_count: dict[str, int] = {}
        # 顺便记下每个异常分类抽到的总样本,后面估算
        per_cls_sampled: dict[str, int] = {}

        for cid, item in seen.items():
            t = item.get("type", 0)
            cur_cls_id = item.get("classification_id", "")
            cur_cls_name = item.get("classification_name", "?")

            # 统计每个异常分类的样本数(用于估算)
            if cur_cls_id in abnormal_cls_ids:
                per_cls_sampled[cur_cls_name] = per_cls_sampled.get(cur_cls_name, 0) + 1

            # 不在异常分类 → 跳过(尊重用户的自定义分类)
            if cur_cls_id not in abnormal_cls_ids:
                continue
            # type 没匹配系统目标 → 跳过(type=999 这种未知值)
            if t not in system_targets:
                continue
            target = system_targets[t]
            # 已经在对的分类
            if cur_cls_id == target["id"]:
                continue

            suggestions.append({
                "collection_id": cid,
                "title": item.get("title", ""),
                "type": t,
                "type_label": TYPE_LABELS.get(t, f"type={t}"),
                "current_classification": cur_cls_name,
                "current_classification_id": cur_cls_id,
                "suggested_classification": target["name"],
                "suggested_classification_id": target["id"],
                "release_year": item.get("release_year", 0),
                "score": item.get("score", 0),
            })
            per_cls_count[cur_cls_name] = per_cls_count.get(cur_cls_name, 0) + 1

        # 5. 估算:从 collection_count 看每个异常分类的真实体量
        coll_count_by_cls = {c.get("name"): c.get("collection_count", 0) for c in all_cls}
        estimates: list[dict] = []
        for cls_name, sampled_mis in per_cls_count.items():
            sampled_in_cls = per_cls_sampled.get(cls_name, 0)
            total_coll = coll_count_by_cls.get(cls_name, 0)
            if sampled_in_cls > 0 and total_coll > 0:
                rate = sampled_mis / sampled_in_cls
                est = round(total_coll * rate)
                estimates.append({
                    "classification": cls_name,
                    "classification_collection_count": total_coll,
                    "sampled_in_class": sampled_in_cls,
                    "sampled_mis_categorized": sampled_mis,
                    "mis_rate": round(rate, 3),
                    "estimated_mis_categorized": est,
                })

        return {
            "ok": True,
            "sampled": len(seen),
            "calls_made": sample_count,
            "abnormal_classifications": [
                c for c in all_cls if c.get("id") in abnormal_cls_ids
            ],
            "system_targets": {str(t): v for t, v in system_targets.items()},
            "target_alternatives": {
                str(t): [{"id": a.get("id"), "name": a.get("name"),
                         "is_system": a.get("is_system"),
                         "collection_count": a.get("collection_count", 0)}
                        for a in alts]
                for t, alts in target_alternatives.items()
            },
            "target_disabled": {
                str(t): [{"id": d.get("id"), "name": d.get("name"),
                          "is_system": d.get("is_system"),
                          "collection_count": d.get("collection_count", 0)}
                         for d in dis]
                for t, dis in target_disabled.items()
            },
            "suggestions": suggestions,
            "per_classification_count": per_cls_count,
            "estimates": estimates,
        }


# ============ 报告渲染 ============

def _render_classifications(audit: dict) -> str:
    """给人看的分类审计报告。"""
    if not audit.get("ok"):
        return f"❌ 分类审计失败: {audit.get('error')}"
    lines = []
    lines.append(f"📂 分类审计 — 共 {audit['total']} 个分类(系统 {audit['system_count']} / 用户 {audit['user_count']})")
    lines.append("")

    if audit["duplicates"]:
        lines.append(f"⚠️ 重名分类 ({len(audit['duplicates'])} 组):")
        for name, items in audit["duplicates"].items():
            lines.append(f"  '{name}' 出现 {len(items)} 次:")
            for c in items:
                sys_tag = "🔒系统" if c.get("is_system") else "  用户"
                lines.append(f"    {sys_tag}  coll={c.get('collection_count', 0)}  id={c.get('id', '')[:8]}")
        lines.append("")

    if audit["user_clashing_with_system"]:
        lines.append(f"⚠️ 用户分类跟系统分类同名 ({len(audit['user_clashing_with_system'])} 个):")
        for c in audit["user_clashing_with_system"]:
            lines.append(f"  '{c.get('name')}' coll={c.get('collection_count', 0)}  id={c.get('id', '')[:8]}")
        lines.append("")

    if audit["empty_classes"]:
        lines.append(f"🗑️ 空分类 ({len(audit['empty_classes'])} 个,series=0 + coll=0):")
        for c in audit["empty_classes"]:
            sys_tag = "🔒系统" if c.get("is_system") else "  用户"
            lines.append(f"  {sys_tag} '{c.get('name')}'  id={c.get('id', '')[:8]}")
        lines.append("")

    if audit["disabled_system_classes"]:
        lines.append(f"🔒 系统分类被关闭 (is_system=1 + is_enable=0)({len(audit['disabled_system_classes'])} 个):")
        for c in audit["disabled_system_classes"]:
            lines.append(f"  '{c.get('name')}' coll={c.get('collection_count', 0)}  id={c.get('id', '')[:8]}")
            lines.append(
                f"    → 用户主动关了 NAS 内置分类,所有 '{c.get('name')}' 影片都在用户自建同名分类里"
            )
        lines.append("")

    if audit["unusual_names"]:
        lines.append(f"❓ 异常名/未启用 ({len(audit['unusual_names'])} 个):")
        for u in audit["unusual_names"]:
            c = u["class"]
            lines.append(f"  '{c.get('name')}' — {'; '.join(u['reasons'])}")
        lines.append("")

    if not any([
        audit["duplicates"], audit["user_clashing_with_system"],
        audit["empty_classes"], audit["disabled_system_classes"],
        audit["unusual_names"],
    ]):
        lines.append("✓ 没有发现明显问题")

    return "\n".join(lines)


def _render_sources(audit: dict) -> str:
    if not audit.get("ok"):
        return f"❌ 源目录审计失败: {audit.get('error')}"
    lines = []
    lines.append(f"📁 源目录审计 — 共 {audit['total']} 个扫描源")
    lines.append(f"  用户池(/sata14/my/): {len(audit['user_pool_dirs'])} 个")
    lines.append(f"  网络挂载(/zspace/extdev/): {len(audit['network_mounts'])} 个")
    lines.append("")

    if audit["suspicious"]:
        lines.append(f"⚠️ 可疑路径 ({len(audit['suspicious'])} 个,可能是误加入影视库):")
        for s in audit["suspicious"]:
            lines.append(f"  {s['path']}")
            lines.append(f"    原因: {s['reason']}")
        lines.append("")

    if audit["network_mounts"]:
        lines.append(f"ℹ️ 网络挂载({len(audit['network_mounts'])} 个,CIFS/SMB 共享,源在另一台机器 192.168.x.x):")
        for d in audit["network_mounts"][:10]:
            lines.append(f"  {d['path']}")
        if len(audit["network_mounts"]) > 10:
            lines.append(f"  ... 还有 {len(audit['network_mounts']) - 10} 个")
        lines.append("")
        lines.append("    NAS API 实测可写(uid 匹配挂载 owner),move 走 NAS 后端")
        lines.append("")

    return "\n".join(lines)


def _render_collections(audit: dict) -> str:
    if not audit.get("ok"):
        return f"❌ 影片抽样失败: {audit.get('error')}"
    lines = []
    lines.append(f"🎬 影片抽样审计 — {audit['calls_made']} 次 randomlist,共 {audit['unique_collections']} 部去重")
    lines.append("")

    # type 分布
    type_dist = audit["type_distribution"]
    lines.append("📊 type 分布:")
    for t, cnt in sorted(type_dist.items(), key=lambda x: -x[1]):
        label = audit["type_labels"].get(t, f"type={t}")
        lines.append(f"  {label} (type={t}): {cnt} 部")
    lines.append("")

    # 按分类聚合
    by_cls = audit["by_classification"]
    lines.append(f"📂 按分类聚合({len(by_cls)} 个分类抽到):")
    for cn, type_dist in sorted(by_cls.items(), key=lambda x: -sum(x[1].values())):
        total = sum(type_dist.values())
        breakdown = " + ".join(
            f"{audit['type_labels'].get(t, f't={t}')}×{cnt}"
            for t, cnt in sorted(type_dist.items(), key=lambda x: -x[1])
        )
        lines.append(f"  '{cn}' = {total} 部  ({breakdown})")
    lines.append("")

    if audit["mismatches"]:
        lines.append(f"⚠️ 分类与 type 不匹配 ({len(audit['mismatches'])} 个分类):")
        for m in audit["mismatches"]:
            lines.append(f"  '{m['classification']}':")
            for issue in m["issues"]:
                lines.append(f"    - {issue}")
        lines.append("")

    return "\n".join(lines)


def _render_moves(audit: dict) -> str:
    """给人看的挪分类建议。"""
    if not audit.get("ok"):
        return f"❌ 挪分类建议失败: {audit.get('error')}"
    lines = []
    lines.append(f"🎯 挪分类建议 — 采样 {audit['sampled']} 部(调 {audit['calls_made']} 次 randomlist)")
    lines.append(f"   异常分类数: {len(audit['abnormal_classifications'])}")
    lines.append("")
    # 覆盖率提示
    if audit["calls_made"] > 0:
        avg_per_call = audit["sampled"] / audit["calls_made"]
        lines.append(
            f"📊 覆盖率:平均每次 randomlist 收到 {avg_per_call:.1f} 部去重"
        )
        if avg_per_call < 5:
            lines.append(
                "   ⚠️ randomlist 当前返回量偏低(NAS 行为波动),建议隔几分钟再跑或加大 --sample"
            )
        lines.append("")

    # 系统目标
    lines.append("📌 目标分类(跳过 is_enable=0 关闭分类;同名优先用户自建且非空):")
    for t_str, info in sorted(audit["system_targets"].items(), key=lambda x: int(x[0])):
        t = int(t_str)
        lines.append(f"  type={t} ({TYPE_LABELS.get(t, '?')}) → {info['name']}  id={info['id'][:8]}")
    # 同名歧义提醒
    alts = audit.get("target_alternatives", {})
    disabled = audit.get("target_disabled", {})
    has_anything = False
    for t_str, alt_list in alts.items():
        if alt_list:
            has_anything = True
            break
    if has_anything:
        lines.append("")
        lines.append("⚠️ 下列 type 存在同名歧义(系统/用户 多份同名分类),报告按上面选的来:")
        for t_str, alt_list in sorted(alts.items(), key=lambda x: int(x[0])):
            if not alt_list:
                continue
            t = int(t_str)
            lines.append(f"  type={t}:")
            for a in alt_list:
                sys_tag = "🔒系统" if a.get("is_system") else "  用户"
                lines.append(f"    {sys_tag} '{a['name']}' coll={a.get('collection_count', 0)}  id={a['id'][:8]}(备选)")
    # 跳过关闭分类提示
    has_disabled = any(disabled.get(t, []) for t in disabled)
    if has_disabled:
        lines.append("")
        lines.append("🔒 跳过的关闭分类(is_enable=0,不当目标):")
        for t_str, dis_list in sorted(disabled.items(), key=lambda x: int(x[0])):
            if not dis_list:
                continue
            t = int(t_str)
            lines.append(f"  type={t}:")
            for d in dis_list:
                sys_tag = "🔒系统" if d.get("is_system") else "  用户"
                lines.append(f"    {sys_tag} '{d['name']}' coll={d.get('collection_count', 0)}  id={d['id'][:8]}")
    lines.append("")

    if not audit["suggestions"]:
        lines.append("✓ 抽样里没发现需要挪的 collection")
        return "\n".join(lines)

    # 按当前分类聚合
    lines.append(f"⚠️ 疑似错放(按当前分类聚合,共 {len(audit['suggestions'])} 部):")
    for cls_name, cnt in sorted(audit["per_classification_count"].items(), key=lambda x: -x[1]):
        target_counter: dict[str, int] = {}
        for s in audit["suggestions"]:
            if s["current_classification"] == cls_name:
                target_counter[s["suggested_classification"]] = (
                    target_counter.get(s["suggested_classification"], 0) + 1
                )
        breakdown = ", ".join(
            f"{tgt}×{c}" for tgt, c in sorted(target_counter.items(), key=lambda x: -x[1])
        )
        lines.append(f"  '{cls_name}': 抽样到 {cnt} 部应挪  →  {breakdown}")
    lines.append("")

    # 估算真实体量
    if audit.get("estimates"):
        lines.append("📐 全量估算(基于抽样率 × 分类 collection_count):")
        for est in audit["estimates"]:
            lines.append(
                f"  '{est['classification']}' 总 {est['classification_collection_count']} 部"
                f" × 抽样错放率 {est['mis_rate']:.1%}"
                f" ≈ {est['estimated_mis_categorized']} 部可能挪"
            )
        lines.append("")

    # 详情
    lines.append(f"📋 详情(前 20 部):")
    for s in audit["suggestions"][:20]:
        lines.append(
            f"  [{s['type_label']}] {s['title']!r} (年={s['release_year']}, 评分={s['score']})"
        )
        lines.append(
            f"    {s['current_classification']} → {s['suggested_classification']}"
        )
        lines.append(f"    collection_id={s['collection_id'][:8]}")
    if len(audit["suggestions"]) > 20:
        lines.append(f"  ... 还有 {len(audit['suggestions']) - 20} 部")
    lines.append("")

    lines.append(
        "ℹ️ NAS 没暴露'挪 collection 分类'API(10 个候选路径都 403),"
        "本命令只生成建议;修复走 pcweb UI 或修改源目录 + classification/rescan。"
    )
    return "\n".join(lines)


def _render_summary(report: dict) -> str:
    """综合报告 — 头部摘要。"""
    cls = report["classifications"]
    src = report["sources"]
    col = report["collections"]
    moves = report.get("moves", {})

    issues = []
    if cls.get("ok"):
        if cls["duplicates"]:
            issues.append(f"重名分类 {len(cls['duplicates'])} 组")
        if cls["user_clashing_with_system"]:
            issues.append(f"用户分类跟系统同名 {len(cls['user_clashing_with_system'])} 个")
        if cls["empty_classes"]:
            issues.append(f"空分类 {len(cls['empty_classes'])} 个")
        if cls["unusual_names"]:
            issues.append(f"异常名 {len(cls['unusual_names'])} 个")
    if src.get("ok") and src["suspicious"]:
        issues.append(f"可疑源目录 {len(src['suspicious'])} 个")
    if col.get("ok") and col["mismatches"]:
        issues.append(f"分类与 type 不匹配 {len(col['mismatches'])} 个")
    if moves.get("ok") and moves["suggestions"]:
        issues.append(f"建议挪分类 {len(moves['suggestions'])} 部(collection 级别)")

    lines = []
    lines.append("=" * 70)
    lines.append("📋 Media Organizer — NAS 极影视诊断报告")
    lines.append("=" * 70)
    lines.append("")
    if issues:
        lines.append(f"⚠️ 发现 {len(issues)} 类问题:")
        for i in issues:
            lines.append(f"  - {i}")
        lines.append("")
    else:
        lines.append("✓ 没有发现明显问题")
        lines.append("")
    lines.append("详细报告见下方各 section。")
    lines.append("")
    return "\n".join(lines)


# ============ Migrator (cross-library file move) ============

class Migrator:
    """按 migration-rules.yaml 检测错放文件并物理迁移。

    用 NAS API(classification/list + /v2/file/list)校验 + 扫描,
    移动走 /v2/file/move。
    """

    def __init__(self, config, nas=None):
        self.config = config
        self.nas = nas or NasClient()

    async def resolve_library_ids(self) -> dict[str, str]:
        """classification/list → {library_name: classification_id}。

        已配的不覆盖;只填空的。
        """
        r = await self.nas.post("/zvideo/classification/list", {})
        if str(r.get("code")) != "200":
            return {}
        by_name = {c.get("name"): c.get("id", "") for c in r.get("data", [])}
        for lib in self.config.libraries:
            if not lib.classification_id and lib.name in by_name:
                lib.classification_id = by_name[lib.name]
        return by_name

    async def scan_files(self) -> dict[str, list[str]]:
        """/v2/file/list(each path) → {lib_name: [abs_file_path]}。

        响应结构: r["data"]["list"][i]["path"]。
        """
        result: dict[str, list[str]] = {}
        for lib in self.config.libraries:
            files: list[str] = []
            for path in lib.expected_host_paths:
                r = await self.nas.post("/v2/file/list", {"path": path, "limit": 10000})
                data = r.get("data") or {}
                items = data.get("list") if isinstance(data, dict) else data
                if not isinstance(items, list):
                    continue
                for it in items:
                    if isinstance(it, dict):
                        # item["path"] 已是 NAS 返回的完整路径(含父目录)
                        p = it.get("path") or f"{path}/{it.get('name', '')}"
                        if p:
                            files.append(p)
            result[lib.name] = files
        return result

    def build_plan(self, files_by_lib: dict[str, list[str]]) -> list[dict]:
        """对每个文件:命中 rule 且当前 lib != target → 候选迁移。"""
        target_path_by_lib: dict[str, str] = {}
        for lib in self.config.libraries:
            if lib.expected_host_paths:
                target_path_by_lib[lib.name] = lib.expected_host_paths[0]

        plan: list[dict] = []
        for lib_name, files in files_by_lib.items():
            for fp in files:
                filename = fp.rsplit("/", 1)[-1]
                rule = match_rule(filename, self.config.move_rules)
                if not rule:
                    continue
                if rule.target == lib_name:
                    continue
                target_path = target_path_by_lib.get(rule.target)
                if not target_path:
                    continue
                dst = f"{target_path}/{filename}"
                plan.append({
                    "src": fp,
                    "dst": dst,
                    "current_lib": lib_name,
                    "target_lib": rule.target,
                    "reason": f"filename={filename!r} 命中 pattern={rule.pattern!r}",
                })
        return plan

    async def execute(
        self, plan: list[dict], dry_run: bool = True, yes: bool = False
    ) -> dict:
        """执行迁移。dry_run 时只打印,不动 NAS。"""
        result = {"applied": [], "skipped": [], "failed": []}

        for i, item in enumerate(plan, 1):
            src, dst = item["src"], item["dst"]
            print(
                f"\n[{i}/{len(plan)}] {src}\n"
                f"   → {dst}\n"
                f"   ({item['current_lib']} → {item['target_lib']}, {item['reason']})"
            )

            if dry_run:
                result["skipped"].append({**item, "reason_skip": "dry-run"})
                continue

            # target exists?
            try:
                info = await self.nas.post("/v2/file/info", {"path": dst})
                if (
                    isinstance(info, dict)
                    and str(info.get("code")) == "200"
                    and info.get("data")
                ):
                    result["skipped"].append({**item, "reason_skip": "target exists"})
                    continue
            except Exception:
                pass

            try:
                dst_dir = dst.rsplit("/", 1)[0]
                r = await self.nas.post(
                    "/v2/file/move", {"paths[]": [src], "to": dst_dir}
                )
                if isinstance(r, dict) and str(r.get("code")) == "200":
                    result["applied"].append(item)
                else:
                    result["failed"].append({**item, "error": str(r.get("msg") or r)})
            except Exception as e:
                result["failed"].append({**item, "error": repr(e)})

        return result


def _render_migrate_plan(plan: list[dict], config: MigrationConfig) -> str:
    if not plan:
        return "✓ 没有发现错放文件"
    lines = [f"📋 拟迁移计划(共 {len(plan)} 条)"]
    lines.append("")
    by_src: dict[str, list[dict]] = {}
    for p in plan:
        by_src.setdefault(p["current_lib"], []).append(p)
    for src_lib in sorted(by_src, key=lambda k: -len(by_src[k])):
        items = by_src[src_lib]
        lines.append(f"  '{src_lib}' → 其它 {len(items)} 条:")
        for it in items[:20]:
            lines.append(f"    [{it['target_lib']}] {it['src']}")
            lines.append(f"      → {it['dst']}")
        if len(items) > 20:
            lines.append(f"    ... 还有 {len(items) - 20} 条")
    lines.append("")
    lines.append("ℹ️ 默认 dry-run。--apply 实际执行移动。")
    return "\n".join(lines)


def _render_migrate_result(result: dict) -> str:
    lines = ["📦 迁移执行结果:"]
    lines.append(f"  ✅ 已迁移: {len(result['applied'])}")
    lines.append(f"  ⏭️  跳过:   {len(result['skipped'])}")
    lines.append(f"  ❌ 失败:   {len(result['failed'])}")
    if result["failed"]:
        lines.append("")
        lines.append("失败明细:")
        for f in result["failed"][:10]:
            lines.append(f"  {f['src']} → {f['dst']}: {f.get('error', '?')}")
    if result["skipped"] and any("reason_skip" in s for s in result["skipped"]):
        skip_reasons: dict[str, int] = {}
        for s in result["skipped"]:
            r = s.get("reason_skip", "?")
            skip_reasons[r] = skip_reasons.get(r, 0) + 1
        lines.append("")
        lines.append("跳过原因:")
        for r, n in sorted(skip_reasons.items(), key=lambda x: -x[1]):
            lines.append(f"  {r}: {n}")
    return "\n".join(lines)


# ============ CLI ============

def _print_or_save(data: dict, output: str | None, human_text: str | None = None):
    if output:
        Path(output).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✓ 已写入 {output}", file=sys.stderr)
    if human_text:
        print(human_text)
    elif not output:
        print(json.dumps(data, ensure_ascii=False, indent=2))


async def _async_main(args):
    mgr = MediaAuditor()
    try:
        if args.cmd == "audit-classifications":
            result = await mgr.audit_classifications()
            text = _render_classifications(result)
            _print_or_save(result, args.output, text)
        elif args.cmd == "audit-sources":
            result = await mgr.audit_sources()
            text = _render_sources(result)
            _print_or_save(result, args.output, text)
        elif args.cmd == "audit-collections":
            result = await mgr.audit_collections(args.sample)
            text = _render_collections(result)
            _print_or_save(result, args.output, text)
        elif args.cmd == "suggest-moves":
            result = await mgr.suggest_moves(args.sample)
            text = _render_moves(result)
            _print_or_save(result, args.output, text)
        elif args.cmd == "audit-all":
            result = await mgr.audit_all(args.sample, getattr(args, "suggest_sample", 20))
            header = _render_summary(result)
            cls_text = _render_classifications(result["classifications"])
            src_text = _render_sources(result["sources"])
            col_text = _render_collections(result["collections"])
            moves_text = _render_moves(result["moves"])
            full_text = "\n".join([header, cls_text, "", src_text, "", col_text, "", moves_text])
            if args.output:
                Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"✓ JSON 写入 {args.output}", file=sys.stderr)
            print(full_text)
        elif args.cmd == "migrate":
            cfg = load_config(args.config)
            mig = Migrator(cfg)
            try:
                await mig.resolve_library_ids()
                files_by_lib = await mig.scan_files()
                plan = mig.build_plan(files_by_lib)

                if args.output:
                    plan_data = {
                        "plan": plan,
                        "libraries": [
                            {"name": l.name, "classification_id": l.classification_id,
                             "expected_host_paths": l.expected_host_paths}
                            for l in cfg.libraries
                        ],
                    }
                    Path(args.output).write_text(
                        json.dumps(plan_data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(f"✓ 计划 JSON 写入 {args.output}", file=sys.stderr)

                print(_render_migrate_plan(plan, cfg))
                if plan and args.apply:
                    print()
                    print("=" * 60)
                    result = await mig.execute(plan, dry_run=False, yes=args.yes)
                    print(_render_migrate_result(result))
                    if args.output:
                        Path(args.output).write_text(
                            json.dumps({"plan": plan, "result": result}, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
            finally:
                await mig.nas.aclose()
        else:
            raise SystemExit(f"unknown cmd: {args.cmd}")
    finally:
        await mgr.nas.aclose()


def main():
    parser = argparse.ArgumentParser(description="media-organizer skill — NAS 极影视只读诊断")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a1 = sub.add_parser("audit-classifications", help="审计分类(重名/空/异常名)")
    a1.add_argument("--output", default=None)
    a2 = sub.add_parser("audit-sources", help="审计源目录(不该在影视库的路径)")
    a2.add_argument("--output", default=None)
    c = sub.add_parser("audit-collections", help="抽样审计 collection(type 分布)")
    c.add_argument("--sample", type=int, default=8, help="randomlist 调用次数,默认 8(理论最多 96 部去重)")
    c.add_argument("--output", default=None)

    sm = sub.add_parser("suggest-moves", help="per-collection 挪分类建议(只读,不写 NAS)")
    sm.add_argument("--sample", type=int, default=20, help="randomlist 采样次数,默认 20")
    sm.add_argument("--output", default=None)

    a = sub.add_parser("audit-all", help="一键全跑,生成综合报告(默认 stdout)")
    a.add_argument("--sample", type=int, default=8, help="randomlist 采样次数(audit-collections)")
    a.add_argument("--suggest-sample", type=int, default=20, help="suggest-moves 的随机采样次数,默认 20")
    a.add_argument("--output", default=None, help="同时把 JSON 详细结果写入文件")

    mg = sub.add_parser("migrate", help="按规则迁移错放文件(dry-run 默认)")
    mg.add_argument("--config", default="migration-rules.yaml",
                    help="规则配置路径,默认 ./migration-rules.yaml")
    mg.add_argument("--apply", action="store_true",
                    help="实际执行移动(默认 dry-run,只打印计划)")
    mg.add_argument("--yes", action="store_true",
                    help="跳过逐条确认(仅 --apply 时有效)")
    mg.add_argument("--output", default=None,
                    help="把计划/结果 JSON 写入文件")

    args = parser.parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()