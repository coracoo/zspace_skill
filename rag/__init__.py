"""RAG 语义搜索包(项目内置,git 追踪)。

提供 3 个 MCP tool 注册:semantic_search / reindex / index_status。
模型:bge-small-zh-v1.5(~100MB,首次自动下载到 ~/.cache/fastembed/)。
向量存储:sqlite-vec + sqlite3,数据在 ~/.cache/zspace-rag/rag.db。

设计:
- 写工具(mkdir/copy/move/remove/notebook_*)完成后,钩子自动增量索引
- 写时增量是同步触发(简化版,不阻塞主流程;复杂场景可改 async)
- 文件白名单:纯文本扩展 + ≤100KB(避免把字幕/日志/二进制当文本)
- chunk 500 字符重叠 50,中文段落级
- N150 限速:DFS 每 200 条一次 /v2/file/list,每页 sleep 100ms
"""
from . import embedder  # noqa: F401  提前 import 让 lru_cache 在加载时初始化
from . import paths, store, chunker
from .mcp_tools import register_rag_tools

__all__ = [
    "register_rag_tools",
    "paths",
    "store",
    "chunker",
    "embedder",
]