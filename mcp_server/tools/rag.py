"""可选 RAG tool(语义搜索 / 重建索引 / 索引状态)。

本模块 import 时尝试 `import rag.mcp_tools`;如果 rag/ 包未安装,ImportError 会向上传播,
被 mcp_server/main.py 的 try/except 吞掉,3 个 RAG tool 就不会注册。
"""
import logging

# 此处 import 失败是本模块的设计信号;让原异常透传
from rag import mcp_tools as _rag_tools_mod  # noqa: F401

from mcp_server import rag_hook
from mcp_server.main import mcp
from mcp_server.perf import _to_json

log = logging.getLogger("zspace-mcp")

# 注册 RAG tool(registry 函数把 3 个 @mcp.tool 注册到本 mcp 实例)
_rag_tools_mod.register_rag_tools(mcp)

# 把 rag 模块塞到 _rag_hook 全局,激活写工具的增量索引钩子
rag_hook._rag_tools = _rag_tools_mod
rag_hook._HAS_RAG = True
log.info("RAG module loaded via tools/rag.py (3 tools registered, hooks enabled)")
