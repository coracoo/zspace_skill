#!/usr/bin/env python3
"""ios-memo-bak Skill:一站式配置 iPhone 备忘录 → NAS 同步。

工作流:
1. 检查 .env 里的 SHORTCUT_KEY(已有就问要不要重生成)
2. 生成 32 hex 随机密钥 + 写 .env
3. 用 ./start.sh dashboard 重启(自动 source .env)
4. 验证 /shortcut/notepad 端点工作(模拟 Shortcut curl)
5. 直接打印 iPhone Shortcut 配置步骤(URL + header + body + 动作链)

⚠️ 警告(写操作):
- 修改 .env(包含敏感信息)
- 重启 dashboard 进程(短时中断 ~3s)

用法:
    python scripts/setup.py            # 一键配置
    python scripts/setup.py --no-restart   # 只生成 key,不重启
"""
import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # lib/ -> skill/ -> skills/ -> repo
ENV_FILE = PROJECT_ROOT / ".env"
APP_MAIN = PROJECT_ROOT / "app" / "main.py"
START_SH = PROJECT_ROOT / "start.sh"


def generate_key() -> str:
    return secrets.token_hex(16)  # 32 hex chars


def read_env_key() -> str:
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("SHORTCUT_KEY="):
            v = line.split("=", 1)[1].strip().strip('"').strip("'")
            return v
    return ""


def update_env_key(key: str) -> None:
    """覆盖或追加 SHORTCUT_KEY 到 .env"""
    if not ENV_FILE.exists():
        raise SystemExit(
            f"❌ {ENV_FILE} 不存在\n"
            f"   先 cp .env.example .env 并填好 NAS_USER / NAS_PASSWORD"
        )
    text = ENV_FILE.read_text()
    new_line = f'SHORTCUT_KEY="{key}"'
    if re.search(r"^SHORTCUT_KEY=.*$", text, re.MULTILINE):
        text = re.sub(r"^SHORTCUT_KEY=.*$", new_line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{new_line}\n"
    ENV_FILE.write_text(text)


def find_dashboard_port() -> int:
    """从 dashboard/app/main.py uvicorn 启动行读端口"""
    if APP_MAIN.exists():
        for line in APP_MAIN.read_text().splitlines():
            m = re.search(r"uvicorn[^\n]*--port (\d+)", line)
            if m:
                return int(m.group(1))
    return 15050  # fallback


def find_nas_lan_ip() -> str:
    """从 .env NAS_HOST 读"""
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("NAS_HOST="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                # 过滤掉 example 占位符
                if "极空间" in v or v == "":
                    return "<NAS_LAN_IP>"
                return v
    return "<NAS_LAN_IP>"


def restart_dashboard() -> bool:
    """用 ./start.sh dashboard 重启(自动 source .env)"""
    pid_file = PROJECT_ROOT / "logs" / "dashboard.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 15)  # SIGTERM
            print(f"  → 已发 SIGTERM 到旧 dashboard PID={pid}")
        except (ProcessLookupError, ValueError):
            pass
        time.sleep(1)
    if not START_SH.exists():
        print("  ⚠ start.sh 不存在,跳过重启(请手动启 dashboard)")
        return False
    print("  → 调用 ./start.sh dashboard...")
    rc = subprocess.run([str(START_SH), "dashboard"], cwd=PROJECT_ROOT).returncode
    return rc == 0


def verify_endpoint(port: int, key: str) -> bool:
    """模拟 iPhone Shortcut curl 验证"""
    url = f"http://localhost:{port}/shortcut/notepad"
    body_dict = {"title": "ios-memo-bak 自动测试", "body": "<p>skill 跑通验证</p>"}
    req = urllib.request.Request(
        url, method="POST",
        data=json.dumps(body_dict).encode(),
        headers={"Content-Type": "application/json", "X-Shortcut-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode()
            print(f"  HTTP {resp.status}: {data[:200]}")
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print(f"  ✗ HTTP {e.code}: {e.read().decode()[:200]}")
        return False
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {str(e)[:100]}")
        return False


def mask_key(k: str) -> str:
    return f"{k[:8]}...{k[-4:]}" if len(k) >= 12 else "***(short)"


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--no-restart", action="store_true",
                   help="只生成 key + 写 .env,不重启 dashboard")
    p.add_argument("--force", action="store_true",
                   help="强制重新生成 key(不询问覆盖)")
    args = p.parse_args()

    print("=" * 60)
    print("ios-memo-bak: iPhone 备忘录 → NAS 同步 配置")
    print("=" * 60)
    print()

    # 1. 检查现有 key
    existing = read_env_key()
    if existing and not args.force:
        print(f"  ℹ️  现有 SHORTCUT_KEY (长度 {len(existing)}): {mask_key(existing)}")
        print(f"     文件: {ENV_FILE}")
        try:
            ans = input("\n  重新生成覆盖? (y/N) > ").strip().lower()
        except EOFError:
            ans = ""
        if ans != "y":
            print("  → 保留现有 key")
            key = existing
        else:
            key = generate_key()
            print(f"  → 生成新 key: {mask_key(key)}")
            update_env_key(key)
    else:
        key = generate_key()
        print(f"  → 生成新 key: {mask_key(key)}")
        update_env_key(key)
    print(f"  → 已写入 {ENV_FILE}")
    print()

    # 2. 重启 dashboard
    if not args.no_restart:
        print("📦 重启 dashboard(让 env 生效)...")
        ok = restart_dashboard()
        if not ok:
            print("  ⚠ 重启失败,请手动跑 ./start.sh dashboard")
        else:
            time.sleep(2)
    print()

    # 3. 验证端点
    port = find_dashboard_port()
    nas_ip = find_nas_lan_ip()
    print(f"🔍 验证端点 http://localhost:{port}/shortcut/notepad ...")
    verify_ok = verify_endpoint(port, key)
    print()

    # 4. 输出 iPhone Shortcut 配置
    print("=" * 60)
    print("📱 iPhone Shortcut 配置(直接复制)")
    print("=" * 60)
    print()
    print("1️⃣  在 iPhone「快捷指令」app 创建新 Shortcut")
    print()
    print("2️⃣  添加以下动作链(自上而下):")
    print()
    print(f'    动作 1: 「共享表单输入」或「获取备忘录」')
    print(f'           - 入口类型: 备忘录(接收整页或选中文本)')
    print(f'    动作 2: 「字典」')
    print(f'           - title  = <备忘录第一行>')
    print(f'           - body   = <富文本 HTML>')
    print(f'    动作 3: 「获取字典内容」→ 格式: JSON')
    print(f'    动作 4: 「获取 URL 内容」')
    print(f'           - URL:        http://{nas_ip}:{port}/shortcut/notepad')
    print(f'           - 方法:      POST')
    print(f'           - 请求头:    Content-Type = application/json')
    print(f'                       X-Shortcut-Key = {key}')
    print(f'           - 请求正文:  动作 3 的 JSON 字典')
    print(f'    动作 5(可选):「显示通知」显示返回结果的 code')
    print()
    print("3️⃣  触发方式:")
    print("    备忘录 app → 选中文本 → 分享 → 选这个 Shortcut")
    print()
    if verify_ok:
        print(f"✅ 配置完成且端点已验证(测试笔记已建)。")
    else:
        print(f"⚠️  配置完成但端点验证失败 — 看上面错误信息排查。")
    print()
    print(f"📋 密钥(配置到 Shortcut): {key}")
    print(f"📋 URL:  http://{nas_ip}:{port}/shortcut/notepad")


if __name__ == "__main__":
    main()