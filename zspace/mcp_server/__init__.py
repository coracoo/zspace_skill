"""MCP server 包。

对外暴露:
- `from zspace.mcp_server import mcp`       — FastMCP 实例(check.py 自检用)
- `from zspace.mcp_server import _to_json`  — tool 统一序列化 helper

协议层 NasClient 现在是顶层包:`from nas import NasClient`(不再经本包转发)。

注意:
- **不**把 `main` 函数 re-export 到包 namespace — 它会遮蔽 `mcp_server.main`
  子模块,导致 tool 文件 `from zspace.mcp_server import main as _main` 拿到的是函数而非模块,
  运行时 `_main.nas` 报 AttributeError。要拿入口函数请用 `from zspace.mcp_server.main import main`。

import 本包会触发 mcp_server/main.py 完整执行 → 所有 tool 注册到 mcp 实例。
"""
from zspace.mcp_server.main import mcp
from zspace.mcp_server.perf import _to_json

__all__ = ["mcp", "_to_json"]
