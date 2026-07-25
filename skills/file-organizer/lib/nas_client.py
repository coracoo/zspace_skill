"""桥接层:从 mcp_server.NasClient 包装出同步接口。

复用 RSA 登录 + cookie 管理,不重复实现。
"""
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # lib/ -> skill/ -> skills/ -> repo
sys.path.insert(0, str(PROJECT_ROOT))


def _load_env():
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        raise SystemExit(
            f"❌ 找不到 {env_file}\n"
            "   请先 cp .env.example .env 并填好 NAS_USER / NAS_PASSWORD"
        )
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

from nas import NasClient  # noqa: E402

# 全局 client + event loop(脚本同步调用)
_client = None
_loop = None


def _get_client() -> NasClient:
    global _client, _loop
    if _client is None:
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _client = NasClient()
        _loop.run_until_complete(_client.login())
    return _client


def run(coro):
    """同步包装 async 调用。"""
    return _loop.run_until_complete(coro)


def get(path: str):
    return run(_get_client().get(path))


def post(path: str, data: dict | None = None):
    return run(_get_client().post(path, data or {}))


__all__ = ["NasClient", "PROJECT_ROOT", "get", "post", "run"]
