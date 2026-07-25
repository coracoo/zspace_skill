"""MCP server 包。

兼容旧 import:
- `from zspace.mcp_server import NasClient`     — skill lib/nas_client.py 桥接层用
- `from zspace.mcp_server import _to_json`      — label-manager skill 桥接层用
- `from zspace.mcp_server import mcp`           — 暴露 FastMCP 实例(可选)

注意:
- **不**把 `main` 函数 re-export 到包 namespace — 它会遮蔽 `mcp_server.main`
  子模块,导致 tool 文件 `from zspace.mcp_server import main as _main` 拿到的是函数而非模块,
  运行时 `_main.nas` 报 AttributeError。要拿入口函数请用 `from zspace.mcp_server.main import main`。

import 本包会触发 mcp_server/main.py 完整执行 → 所有 tool 注册到 mcp 实例。
"""
from zspace.nas import NasClient  # 重导出,skill 兼容(`from zspace.mcp_server import NasClient`)
from zspace.mcp_server.main import mcp
from zspace.mcp_server.perf import _to_json

__all__ = ["NasClient", "mcp", "_to_json"]
