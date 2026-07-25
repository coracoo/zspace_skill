"""RAG 写时增量钩子。

写 tool(mkdir/rename/move/copy/remove/notebook_*)调用 _rag_hook 通知 RAG 模块。
rag 包未安装时静默 no-op。

设计要点(沿用原 mcp_server.py:487-516 的语义):
- `_HAS_RAG` / `_rag_tools` 模块级全局;main.py 在 FastMCP 实例化后尝试 import rag.mcp_tools
- `_rag_hook(hook_name, *args)` 仅当 `_HAS_RAG=True` 且 `args[0]` 是 NAS 成功响应(code==200)时触发
- 任何 RAG 异常都被吞掉(避免索引失败影响已成功的 NAS 写入)
"""
import logging

log = logging.getLogger("zspace-mcp")

_HAS_RAG = False
_rag_tools = None


def _rag_hook(hook_name: str, *args) -> None:
    """安全调用 RAG 钩子:仅在 NAS 写入成功(code==200)时触发,
    且吞掉 RAG 自身异常,避免索引失败影响已成功的 NAS 写入。

    hook_name 是 _rag_tools 上的方法名(字符串),延迟到此处 getattr,
    避免 _HAS_RAG=False 时调用点求值 _rag_tools 触发 NameError。"""
    if not _HAS_RAG:
        return
    resp = args[0] if args else None
    if not (isinstance(resp, dict) and str(resp.get("code")) == "200"):
        return
    try:
        fn = getattr(_rag_tools, hook_name)
        fn(*args)
    except Exception as e:
        log.warning("RAG hook %s failed: %s", hook_name, e)
