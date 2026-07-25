"""配置常量(从环境变量读,有默认值)。

环境变量:
- RAG_DB_PATH           sqlite 文件路径(默认 /app/data/rag.db)
- FASTEMBED_CACHE_DIR   bge 模型 cache(默认 ~/.cache/fastembed/)
- RAG_SCAN_ROOTS        扫描根,逗号分隔(默认 /sata14/my/data/)
- RAG_WHITELIST_EXTS    白名单扩展,逗号分隔
- RAG_MAX_FILE_SIZE_KB  单文件大小上限(默认 100)
- RAG_CHUNK_SIZE        chunk 字符数(默认 500)
- RAG_CHUNK_OVERLAP     重叠字符(默认 50)
- RAG_SLEEP_BETWEEN_PAGES  扫描限速 ms(默认 100,N150 友好)
- RAG_HOST              FastAPI 监听 host(默认 0.0.0.0)
- RAG_PORT              FastAPI 监听端口(默认 8000)
"""
import os
from pathlib import Path

# 模型
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
EMBED_DIM = 512

# 数据路径
DB_PATH = Path(os.environ.get("RAG_DB_PATH", "/app/data/rag.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# 模型 cache(fastembed 用 HF cache 协议,放 ~/.cache/fastembed)
FASTEMBED_CACHE_DIR = os.environ.get(
    "FASTEMBED_CACHE_DIR",
    str(Path.home() / ".cache" / "fastembed"),
)
# fastembed 用 HF_HOME 环境变量管 cache
os.environ.setdefault("HF_HOME", FASTEMBED_CACHE_DIR)
os.environ.setdefault("FASTEMBED_CACHE_DIR_OVERRIDE", FASTEMBED_CACHE_DIR)

# 扫描配置
SCAN_ROOTS = [
    r.strip()
    for r in os.environ.get("RAG_SCAN_ROOTS", "/sata14/my/data/").split(",")
    if r.strip()
]

WHITELIST_EXTS = frozenset(
    e.strip().lower().lstrip(".")
    for e in os.environ.get(
        "RAG_WHITELIST_EXTS",
        ".py,.md,.txt,.json,.yaml,.yml,.conf,.csv,.log,.mdx,.rst,.tex,.html,.htm,.xml,.ini,.toml,.cfg",
    ).split(",")
    if e.strip()
)

MAX_FILE_SIZE = int(os.environ.get("RAG_MAX_FILE_SIZE_KB", "100")) * 1024

# Chunking
CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "50"))

# N150 限速
SLEEP_BETWEEN_PAGES = float(os.environ.get("RAG_SLEEP_BETWEEN_PAGES", "100")) / 1000.0
LIST_PAGE_SIZE = 200  # 单次 iterdir/glob 一批

# FastAPI
HOST = os.environ.get("RAG_HOST", "0.0.0.0")
PORT = int(os.environ.get("RAG_PORT", "8000"))

# 索引时单文件上限(NAS 上扫的,> 这个的跳过)
MAX_EMBED_FILES_PER_REINDEX = 100_000  # 安全阀,防扫到爆

# API
DEFAULT_TOP_K = 10
MAX_TOP_K = 50
SAVE_BATCH_SIZE = 50  # 主项目 save_file_label 的 NAS 限速