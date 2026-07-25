"""桥接层:从 mcp_server.py 复用 NasClient。

跟 label-manager skill 的 lib/nas_client.py 完全一样的模式。
复用而不重复实现 RSA 登录 + cookie 管理。
"""
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

from zspace.mcp_server import NasClient  # noqa: E402

__all__ = ["NasClient", "PROJECT_ROOT"]