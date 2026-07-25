"""桥接层:复用顶层 `nas` 包的 NasClient,不在 skill 目录里重复实现 RSA 登录。

路径处理:
  - 这个文件在 skills/label-manager/lib/nas_client.py
  - 项目根 = parents[3](lib → label-manager → skills → repo)

env 加载顺序:
  - `nas` 包在 import 时就读 env(NAS_USER / NAS_PASSWORD / NAS_HOST)
  - 必须在 `from nas import NasClient` 之前加载 .env
  - 所以 _load_env() 放在 import 之前
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # lib/ -> skill/ -> skills/ -> repo
sys.path.insert(0, str(PROJECT_ROOT))


def _load_env():
    """从 .env 加载 env vars,不依赖 python-dotenv。

    用 setdefault 而不是直接 set — shell 里如果已经 export 了,优先级最高,
    .env 只兜底。
    """
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


_load_env()  # 必须在 import zspace.mcp_server 之前

from nas import NasClient

__all__ = ["NasClient", "PROJECT_ROOT"]