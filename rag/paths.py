"""RAG 资源路径常量。

数据存放:`~/.cache/zspace-rag/rag.db`(~50MB / 1000 chunks),git ignore。
"""
import os
from pathlib import Path

CACHE_DIR = Path(os.environ.get("ZSPACE_RAG_CACHE", "~/.cache/zspace-rag")).expanduser()
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = CACHE_DIR / "rag.db"

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
EMBED_DIM = 512

# 文本文件白名单(<100KB 才索引,避免把电影字幕/日志/二进制当文本)
WHITELIST_EXTS = frozenset({
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".conf",
    ".csv", ".log", ".mdx", ".rst", ".tex", ".html", ".htm",
    ".xml", ".ini", ".toml", ".cfg",
})

MAX_FILE_SIZE = 100 * 1024  # 100KB

CHUNK_SIZE = 500      # 字符
CHUNK_OVERLAP = 50     # 重叠字符,保留上下文