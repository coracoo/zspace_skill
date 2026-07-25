"""FastAPI app — 5 个端点(/search /reindex /index /unindex /status)。

启动顺序:
1. init_db() 建表
2. uvicorn 起服务
3. /search 第一次调用时 lru_cache 加载 bge 模型(~3s)
"""
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException

from . import config
from .chunker import chunk_text
from .config import DB_PATH, MAX_FILE_SIZE, WHITELIST_EXTS
from .embedder import embed_query, embed_texts
from .models import (
    IndexReq,
    IndexResp,
    ReindexReq,
    ReindexResp,
    SearchReq,
    SearchResp,
    StatusResp,
    UnindexReq,
    UnindexResp,
)
from .store import (
    clear_scope,
    count_chunks,
    count_chunks_by_scope,
    delete_chunks_by_source,
    get_meta,
    init_db,
    insert_chunks_batch,
    search,
    set_meta,
)

# 日志(stderr,匹配 uvicorn 默认)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("nas-rag")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表 + 记录模型信息。"""
    init_db()
    set_meta("model", config.MODEL_NAME)
    log.info(
        "nas-rag-server starting (model=%s dim=%d db=%s)",
        config.MODEL_NAME, config.EMBED_DIM, DB_PATH,
    )
    log.info("scan roots: %s", config.SCAN_ROOTS)
    log.info("whitelist exts: %s", sorted(WHITELIST_EXTS))
    log.info("max file size: %d KB", MAX_FILE_SIZE // 1024)
    yield
    log.info("nas-rag-server shutdown")


app = FastAPI(
    title="nas-rag-server",
    description="NAS 端 RAG 服务(bge-small-zh-v1.5 + sqlite-vec)。详见 docs/03-API.md",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/status", response_model=StatusResp)
def get_status() -> StatusResp:
    """索引概况。"""
    db_size_mb = (DB_PATH.stat().st_size / 1024 / 1024) if DB_PATH.exists() else 0
    return StatusResp(
        model=get_meta("model") or config.MODEL_NAME,
        embed_dim=config.EMBED_DIM,
        total_chunks=count_chunks(),
        db_size_mb=round(db_size_mb, 2),
        db_path=str(DB_PATH),
        last_reindex=get_meta("last_reindex"),
        scope_stats=count_chunks_by_scope(),
    )


@app.post("/search", response_model=SearchResp)
def post_search(req: SearchReq) -> SearchResp:
    """语义搜索(N150 ~50ms embed + <5ms KNN)。"""
    if req.scope not in ("all", "files", "notebooks"):
        req.scope = "all"
    try:
        q_vec = embed_query(req.query)
    except Exception as e:
        log.exception("embed failed")
        raise HTTPException(status_code=500, detail=f"embed_query failed: {e}")
    results = search(q_vec, scope=req.scope, top_k=req.top_k)
    return SearchResp(
        query=req.query, scope=req.scope, count=len(results), results=results,
    )


@app.post("/reindex", response_model=ReindexResp)
def post_reindex(req: ReindexReq) -> ReindexResp:
    """触发 reindex(同步阻塞,大目录要几分钟)。"""
    if req.scope not in ("all", "files", "notebooks"):
        req.scope = "files"

    # full=True 先清表
    if req.full:
        if req.scope == "all":
            removed = clear_scope(None)
        else:
            source_type = "file" if req.scope == "files" else "notebook"
            removed = clear_scope(source_type)
        log.info("cleared %d chunks (scope=%s, full=%s)", removed, req.scope, req.full)

    # 实际扫
    if req.scope in ("all", "files"):
        from .scanner import reindex_files
        stats = reindex_files()
    else:
        # notebooks scope 暂未实现(Phase 6)
        stats = {"note": "notebooks scope not implemented yet (Phase 6)"}

    set_meta("last_reindex", datetime.now().isoformat(timespec="seconds"))
    return ReindexResp(
        scope=req.scope,
        full=req.full,
        stats=stats,
        total_chunks=count_chunks(),
        completed_at=datetime.now().isoformat(timespec="seconds"),
    )


@app.post("/index", response_model=IndexResp)
def post_index(req: IndexReq) -> IndexResp:
    """单条索引(写时增量钩子用)。先删同 source_path 旧 chunks,再插新的。"""
    # 先清旧的(让 hook 重写覆盖)
    delete_chunks_by_source(req.source_type, req.source_path)
    chunks = chunk_text(req.file_content)
    if not chunks:
        return IndexResp(chunks_count=0)
    embs = embed_texts(chunks)
    items = [(req.source_type, req.source_path, snippet, vec)
             for snippet, vec in zip(chunks, embs)]
    n = insert_chunks_batch(items)
    log.info("indexed %d chunks for %s:%s", n, req.source_type, req.source_path)
    return IndexResp(chunks_count=n)


@app.post("/unindex", response_model=UnindexResp)
def post_unindex(req: UnindexReq) -> UnindexResp:
    """反索引(删某 source 的所有 chunks)。"""
    n = delete_chunks_by_source(req.source_type, req.source_path)
    return UnindexResp(removed=n)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.server:app",
        host=config.HOST,
        port=config.PORT,
        workers=1,
        log_level="info",
    )