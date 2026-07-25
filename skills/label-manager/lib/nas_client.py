"""桥接层:从 mcp_server.py 复用 NasClient,不在 skill 目录里重复实现 RSA 登录。

为什么要这个文件:
  - mcp_server.py 顶层实例化了 FastMCP + 注册 57 个 tool
  - 直接 `from zspace.mcp_server import NasClient` 会触发这些初始化(实测 0.77s 冷启动,可接受)
  - 把 import 集中到这里,避免散在脚本各处,也方便以后想抽出来

路径处理:
  - 这个文件在 skills/label-manager/lib/nas_client.py
  - 项目根 = parents[3](lib → label-manager → skills → skills → repo)

env 加载顺序:
  - mcp_server.py 顶层会读 env(NAS_USER / NAS_PASSWORD)
  - 必须在 import zspace.mcp_server 之前加载 .env
  - 所以 _load_env() 放在 from zspace.mcp_server import 之前
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

from zspace.mcp_server import NasClient, _to_json  # noqa: E402

__all__ = ["NasClient", "_to_json", "PROJECT_ROOT"]