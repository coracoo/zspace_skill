#!/usr/bin/env python3
"""百度网盘 OAuth 登录 CLI(一次性,登录后 token 长期保存在 NAS 后端)

流程:
1. 调 /znetdisk/auth/check 拿 OAuth URL(client_id 已在 NAS 注册)
2. webbrowser.open 自动开浏览器,你在百度登录并授权
3. 百度显示授权码(code),复制粘贴回 CLI
4. 调 /znetdisk/auth/token {app:"baidu", code:...} 完成 token 交换
5. 调 /znetdisk/auth/userinfo 验证

限制:
- 百度 OAuth client_id 是极空间官方注册的,redirect_uri 固定为 oob
  → 不能改 callback URL,必须手动复制 code 一次
- token(refresh_token)默认 5 年有效,只要不 logout 一直能用
- 阿里云盘不走这个 CLI(其 OAuth 入口未破,只能 NAS UI 登录)

用法:
    .venv/bin/python scripts/netdisk_login.py
"""
import argparse
import asyncio
import json
import os
import sys
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from zspace.mcp_server import NasClient  # noqa: E402


async def fetch_oauth_url(nas: NasClient) -> dict:
    """调 auth/check,返回 {is_login, url}"""
    r = await nas.post("/znetdisk/auth/check", {})
    if str(r.get("code")) != "200":
        raise RuntimeError(f"auth/check 失败: code={r.get('code')} msg={r.get('msg')}")
    data = r.get("data") or {}
    return {"is_login": data.get("is_login"), "url": data.get("url", "")}


async def exchange_token(nas: NasClient, code: str) -> dict:
    """调 auth/token,用 code 换 token"""
    r = await nas.post("/znetdisk/auth/token", {"app": "baidu", "code": code})
    return r


async def fetch_userinfo(nas: NasClient) -> dict:
    """登录后调 userinfo 验证"""
    r = await nas.post("/znetdisk/auth/userinfo", {})
    return r


async def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--no-browser", action="store_true", help="不自动开浏览器,只打印 URL")
    args = p.parse_args()

    print("→ 登录 NAS(用 .env 里的 NAS_USER/NAS_PASSWORD)...")
    nas = NasClient()
    await nas.login()
    print(f"  ✓ NAS 登录成功 user={nas._profile.get('username')}")
    print()

    print("→ 检查百度网盘登录态...")
    info = await fetch_oauth_url(nas)
    if info["is_login"]:
        print("  ✓ 百度网盘已经登录过了,无需重复登录")
        print("  如要换账号,先在 NAS UI 退出百度网盘")
        r = await fetch_userinfo(nas)
        print(f"  userinfo: {json.dumps(r.get('data') or {}, ensure_ascii=False)[:200]}")
        return

    url = info["url"]
    print(f"  ✗ 未登录")
    print()
    print("→ OAuth 授权 URL:")
    print(f"  {url}")
    print()

    if not args.no_browser:
        print("→ 在浏览器打开授权页(自动)...")
        try:
            webbrowser.open(url)
            print("  ✓ 浏览器已打开")
        except Exception as e:
            print(f"  ⚠ 自动打开失败({e}),请手动复制上面的 URL 到浏览器")
    else:
        print("  (--no-browser 模式,请手动复制 URL)")

    print()
    print("在百度页面登录账号并授权后,会显示一串【授权码】(类似 abcdef1234567890)。")
    print("复制那串授权码,粘贴到下面:")
    print()
    try:
        code = input("授权码 > ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n未输入授权码,退出")
        return 1

    if not code:
        print("授权码为空,退出")
        return 1
    if len(code) < 10:
        print(f"授权码太短(长度 {len(code)}),百度 OAuth code 通常是 32 字符左右")
        try:
            confirm = input("确认提交?(y/N) > ").strip().lower()
        except EOFError:
            print("\n输入中断,退出")
            return 1
        if confirm != "y":
            print("取消")
            return 1

    print()
    print("→ 用授权码换 token...")
    r = await exchange_token(nas, code)
    if str(r.get("code")) != "200":
        print(f"  ✗ 失败: code={r.get('code')} msg={r.get('msg')}")
        print(f"  完整返回: {json.dumps(r, ensure_ascii=False)[:300]}")
        return 1
    print(f"  ✓ 成功: {json.dumps(r.get('data') or {}, ensure_ascii=False)[:200]}")

    print()
    print("→ 验证 userinfo...")
    r = await fetch_userinfo(nas)
    if str(r.get("code")) == "200":
        data = r.get("data") or {}
        print(f"  ✓ 登录成功")
        print(f"    用户: {data.get('username') or data.get('name') or data.get('uk', '(未知)')}")
        print(f"    完整: {json.dumps(data, ensure_ascii=False)[:300]}")
        print()
        print("✅ 百度网盘登录完成。后续 MCP tool 调用会自动用这个 token。")
        print("   token 由 NAS 后端管理(refresh_token 默认 5 年有效)")
    else:
        print(f"  ⚠ userinfo 验证失败: code={r.get('code')} msg={r.get('msg')}")
        print(f"  (但 token 交换已成功,可能需要等几秒同步)")


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc or 0)
