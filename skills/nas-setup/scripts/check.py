#!/usr/bin/env python3
"""nas-setup: 检查 NAS 环境是否就绪。

检查清单:
1. .env 是否存在 + 关键变量是否填了
2. NAS 能否登录(whoami 返回 200)
3. RAG daemon 是否在线(index_status 返回 chunks)

返回:汇总报告,用 ✅/❌/⚠️ 标记每项。

用法:
    python .claude/skills/nas-setup/scripts/check.py
    python .claude/skills/nas-setup/scripts/check.py --json  # JSON 格式输出
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # scripts/ -> nas-setup/ -> skills/ -> .claude/ -> repo
ENV_FILE = PROJECT_ROOT / ".env"

# 必需/推荐的 env 变量
REQUIRED_VARS = ["NAS_HOST", "NAS_USER", "NAS_PASSWORD"]
RECOMMENDED_VARS = ["NAS_DEVICE_ID", "NAS_RAG_URL"]
OPTIONAL_VARS = ["KEY_SSH", "NAS_SSH_PORT"]  # 仅 perf_snapshot 需要,不影响其他功能


def _load_env_to_os() -> None:
    """把 .env 加载到 os.environ(NasClient 需要)。"""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^(\w+)\s*=\s*["\']?(.*?)["\']?\s*$', line)
        if m:
            k = m.group(1)
            if k not in os.environ:  # 不覆盖已有 env
                os.environ[k] = m.group(2).strip()


def check_mcp() -> dict:
    """检查 MCP Python 包是否可导入。"""
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        import httpx       # noqa: F401
        import cryptography # noqa: F401
        from zspace.nas import NasClient  # noqa: F401
        from zspace.mcp_server import mcp # noqa: F401
        return {"ok": True, "tools": len(mcp._tool_manager._tools)}
    except ImportError as e:
        return {"ok": False, "msg": f"MCP 包未安装: {e}. 请 pip install -e . 或 ./start.sh deps"}


def check_env() -> dict:
    """检查 .env 文件是否存在 + 变量是否填写。"""
    results = {"checked": True, "issues": [], "vars": {}}

    if not ENV_FILE.exists():
        results["checked"] = False
        results["issues"].append(f".env 文件不存在: {ENV_FILE}")
        results["issues"].append("请 cp .env.example .env 并填入真实值")
        return results

    # 解析 .env(简单版:key=value,忽略注释)
    env_vars: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^(\w+)\s*=\s*["\']?(.*?)["\']?\s*$', line)
        if m:
            env_vars[m.group(1)] = m.group(2).strip()

    for var in REQUIRED_VARS + RECOMMENDED_VARS + OPTIONAL_VARS:
        val = env_vars.get(var, "")
        is_empty = not val or "你的" in val or "<" in val  # 占位符也算空
        # 遮蔽敏感值
        masked = val
        if not is_empty and var in ("NAS_PASSWORD", "KEY_SSH"):
            masked = "***(" + str(len(val)) + "位)"
        elif not is_empty:
            masked = val[:3] + "..." + val[-2:] if len(val) > 5 else val[:3] + "..."
        results["vars"][var] = {
            "value": masked,
            "ok": not is_empty,
        }
        if is_empty and var in REQUIRED_VARS:
            results["issues"].append(f"必需变量 {var} 未填写(当前: {val or '(空)'})")
        elif is_empty and var in RECOMMENDED_VARS:
            results["issues"].append(f"推荐变量 {var} 未设置({'(空)' if not val else '占位符'})")
        elif is_empty and var in OPTIONAL_VARS:
            pass  # 可选变量,不报 issue

    results["ok"] = len(results["issues"]) == 0
    return results


def check_login() -> dict:
    """尝试 NAS 登录,调 whoami。"""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from zspace.mcp_server import NasClient
        import asyncio

        async def _try():
            nas = NasClient()
            await nas.login()
            r = await nas.post("/v2/file/list", {"folderId": 0, "path": "/sata14/my/data/", "start": 0, "num": 1})
            return r

        r = asyncio.run(_try())
        code = str(r.get("code", ""))
        if code == "200":
            profile = nas._profile if hasattr(NasClient.__init__, '_profile') else {}
            return {
                "ok": True,
                "code": code,
                "user": f"已登录(NAS_HOST={os.environ.get('NAS_HOST','')})",
            }
        else:
            msg = r.get("msg", "")
            return {"ok": False, "code": code, "msg": msg}
    except Exception as e:
        msg = str(e)
        return {"ok": False, "code": "EXCEPTION", "msg": msg[:200]}


def check_rag() -> dict:
    """尝试连 RAG daemon 拿 index_status。"""
    url = os.environ.get("NAS_RAG_URL", "http://" + os.environ.get("NAS_HOST", "127.0.0.1") + ":8000")
    try:
        import urllib.request
        req = urllib.request.Request(url + "/status", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return {
                "ok": True,
                "url": url,
                "chunks": data.get("total_chunks", 0),
                "model": data.get("model", "?"),
            }
    except Exception as e:
        return {"ok": False, "url": url, "msg": str(e)[:150]}


def main():
    p = argparse.ArgumentParser(description="NAS 环境检查")
    p.add_argument("--json", action="store_true", help="JSON 格式输出")
    p.add_argument("--no-rag", action="store_true", help="跳过 RAG daemon 检查")
    args = p.parse_args()

    # 先加载 .env 到 os.environ(后续 NasClient + RAG 检查需要)
    _load_env_to_os()

    results = {
        "mcp": check_mcp(),
        "env": check_env(),
        "login": check_login(),
        "rag": None if args.no_rag else check_rag(),
    }

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if all(
            v.get("ok", False) for v in results.values() if v is not None
        ) else 1

    # 人类可读输出
    width = 40
    print("=" * width)
    print("NAS 环境检查")
    print("=" * width)
    print()

    # 0. MCP
    mcp = results["mcp"]
    if mcp["ok"]:
        print(f"✅ MCP 已安装({mcp['tools']} tools)")
    else:
        print(f"❌ MCP 未安装: {mcp.get('msg', '?')}")
        print(f"   → pip install -e .  或  ./start.sh deps")
    print()

    # 1. .env
    env = results["env"]
    if env["checked"] and env["ok"]:
        print("✅ .env 配置: 完整")
    elif env["checked"]:
        print("⚠️  .env 配置: 不完整")
    else:
        print("❌ .env 配置: 缺失")
    for issue in env.get("issues", []):
        print(f"   → {issue}")
    print(f"   vars: {json.dumps(env.get('vars', {}), ensure_ascii=False)}")
    print()

    # 2. 登录
    login = results["login"]
    if login["ok"]:
        print(f"✅ NAS 登录: 通({login.get('user', '?')})")
    else:
        code = login.get("code", "?")
        if code == "N001414":
            print(f"❌ NAS 登录: N001414 短信验证")
            print(f"   → 把真实 device_id 填到 .env 的 NAS_DEVICE_ID")
        elif code == "N001200":
            print(f"❌ NAS 登录: N001200 账号格式不对")
            print(f"   → RSA 公钥不匹配(固件版本 vs nas/auth.py)")
        else:
            print(f"❌ NAS 登录: {code} {login.get('msg', '')[:80]}")
    print()

    # 3. RAG
    if results["rag"] is not None:
        rag = results["rag"]
        if rag["ok"]:
            print(f"✅ RAG daemon: {rag['url']} ({rag['model']}, {rag['chunks']} chunks)")
        else:
            print(f"⚠️  RAG daemon: {rag['url']} 不通")
            print(f"   → smart-tagger 降级到文件名匹配")
            print(f"   → 启动: cd nas-rag-server && docker compose up -d")
        print()

    # 汇总
    all_ok = env["ok"] and login["ok"] and mcp["ok"]
    rag_ok = results["rag"] is None or results["rag"].get("ok", False)
    print("=" * width)
    if all_ok and rag_ok:
        print("✅ 全部就绪,可以使用所有 skill")
    elif all_ok:
        print("⚠️  NAS 登录就绪,RAG 不可用(语义搜索降级)")
    else:
        print("❌ 有问题,先修上面的红叉")
    print("=" * width)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
