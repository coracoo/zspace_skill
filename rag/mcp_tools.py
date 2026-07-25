"""RAG MCP tool 注册。

被 mcp_server/tools/rag.py 在 main.py 之后调,注册 3 个 tool:
- semantic_search:语义搜 NAS 内容
- reindex:重建索引(同步跑,N150 限速)
- index_status:看索引概况

设计:
- 注册前调 init_db() 确保表存在
- 写时增量钩子在 hooks.py,本模块注册到 mcp_server.rag_hook 全局
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .embedder import embed_query, embed_texts
from .hooks import (
    rag_on_file_delete,
    rag_on_file_move,
    rag_on_file_write,
    rag_on_notebook_delete,
    rag_on_notebook_write,
)
from .paths import DB_PATH, MAX_FILE_SIZE, WHITELIST_EXTS
from .store import (
    count_chunks,
    get_meta,
    init_db,
    search,
    set_meta,
)

log = logging.getLogger("zspace-rag")

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register_rag_tools(mcp: "FastMCP") -> None:
    """在 mcp_server/tools/rag.py 里被调,注册 3 个 tool 到给定 FastMCP 实例。"""
    init_db()
    set_meta("model", "BAAI/bge-small-zh-v1.5")
    log.info("registering 3 RAG tools (semantic_search / reindex / index_status)")

    @mcp.tool()
    async def semantic_search(query: str, scope: str = "all", top_k: int = 10) -> str:
        """🔍 语义搜索:NAS 里的笔记 body + 文件名 + 文本文件内容。

        跟 `notebook_search`(关键词)和 `list_files`(列路径)不同 — 这个是**理解语义**的:
        - "一年级教材" 能找到文件名不含这词但内容是小学教辅的 PDF
        - "docker swarm 笔记" 能找到提到 K8s 编排的笔记
        - "报销单" 能找到文件名是"expense_2024.pdf"的文件

        query:  自然语言(中文效果最好,bge 模型中文 SOTA)
        scope:  all / files / notebooks(默认 all)
        top_k:  返回数量(默认 10,越大越慢)

        返回:[{source_type, source_path, snippet, distance}, ...](distance 越小越相关)"""
        if scope not in ("all", "files", "notebooks"):
            scope = "all"
        try:
            q_vec = embed_query(query)
        except Exception as e:
            return json.dumps({"error": f"embedding 失败:{e}"}, ensure_ascii=False)
        results = search(q_vec, scope=scope, top_k=top_k)
        return json.dumps({
            "query": query,
            "scope": scope,
            "count": len(results),
            "results": results,
        }, ensure_ascii=False)

    @mcp.tool()
    async def reindex(scope: str = "all", full: bool = False) -> str:
        """🔄 重建索引(N150 限速,同步执行,慢)。

        scope: all / notebooks / files(默认 all)
        full:  True=清空重建,False=只索引 hash 没命中的(增量)

        ⚠️ 大目录(N 万文件)要几分钟。进度会打到 stderr。

        注意:写 tool(mkdir/copy/move/remove/notebook_*)会自动触发增量索引,
        不需要每次都跑这个。只在以下情况用:
        - 文件**内容**被 NAS 端直接改(没走 MCP)
        - 第一次初始化
        - full=True 全量重建"""
        if scope not in ("all", "files", "notebooks"):
            scope = "all"

        if full:
            log.warning("full reindex requested, scope=%s", scope)
            from .store import get_conn
            conn = get_conn()
            try:
                if scope == "all":
                    conn.execute("DELETE FROM vec_index")
                    conn.execute("DELETE FROM chunks")
                else:
                    source_type = "file" if scope == "files" else "notebook"
                    # 两段式:先清 vec_index 关联行,再清 chunks
                    conn.execute(
                        "DELETE FROM vec_index WHERE chunk_id IN "
                        "(SELECT id FROM chunks WHERE source_type=?)",
                        (source_type,),
                    )
                    conn.execute(
                        "DELETE FROM chunks WHERE source_type=?",
                        (source_type,),
                    )
                conn.commit()
            finally:
                conn.close()

        indexed = await _do_reindex(scope)
        set_meta("last_reindex", datetime.now().isoformat(timespec="seconds"))
        return json.dumps({
            "scope": scope,
            "full": full,
            "indexed_files": indexed,
            "total_chunks": count_chunks(),
            "completed_at": datetime.now().isoformat(timespec="seconds"),
        }, ensure_ascii=False)

    @mcp.tool()
    async def index_status() -> str:
        """📊 索引概况:模型/条数/库大小/最后 reindex 时间。"""
        db_size_mb = (DB_PATH.stat().st_size / 1024 / 1024) if DB_PATH.exists() else 0
        return json.dumps({
            "model": get_meta("model") or "BAAI/bge-small-zh-v1.5",
            "total_chunks": count_chunks(),
            "db_size_mb": round(db_size_mb, 2),
            "db_path": str(DB_PATH),
            "last_reindex": get_meta("last_reindex"),
            "embed_dim": 512,
            "whitelist_exts": sorted(WHITELIST_EXTS),
            "max_file_size_kb": MAX_FILE_SIZE // 1024,
        }, ensure_ascii=False)

    # 注册到 mcp_server.rag_hook 全局,激活写工具的增量索引
    from mcp_server import rag_hook
    rag_hook._rag_tools = _HooksAdapter()
    rag_hook._HAS_RAG = True
    log.info("RAG hooks activated: rag_on_file_write/delete/move, rag_on_notebook_write/delete")


def _do_reindex(scope: str) -> int:
    """实际跑 reindex,返回索引的文件数。

    N150 限速 100ms/请求 + 进度输出到 stderr。
    scope=files:DFS 扫 NAS 池
    scope=notebooks:拉 location=2 笔记本列表
    scope=all:两者都做
    """
    indexed = 0
    if scope in ("all", "files"):
        indexed += _reindex_files()
    if scope in ("all", "notebooks"):
        indexed += _reindex_notebooks()
    return indexed


async def _do_reindex(scope: str) -> int:
    """async 版 — 在 MCP tool 里用 await,直接 await nas.post。"""
    indexed = 0
    if scope in ("all", "files"):
        indexed += await _reindex_files_async()
    if scope in ("all", "notebooks"):
        indexed += await _reindex_notebooks_async()
    return indexed


async def _reindex_files_async() -> int:
    """DFS 扫 NAS 池,白名单文本文件 embed+入库。N150 限速。"""
    from mcp_server import main as _main
    nas = _main.nas
    if nas is None:
        log.warning("_main.nas is None, MCP server not started yet")
        return 0

    try:
        pools_resp = await nas.post("/zspool/info", {})
    except Exception as e:
        log.warning("zspool/info failed: %s", e)
        return await _reindex_via_mcp_async()

    pools = pools_resp.get("data", {}).get("pools", []) if isinstance(pools_resp, dict) else []
    if not pools:
        return await _reindex_via_mcp_async()

    indexed = 0
    for pool in pools:
        pool_name = pool.get("name", "")
        root = f"/{pool_name}/my/data/"
        log.info("reindex files: scanning pool=%s root=%s", pool_name, root)
        indexed += await _dfs_scan_and_index_async(nas, root)
    return indexed


async def _reindex_via_mcp_async() -> int:
    """fallback:常见池名列表逐个扫。"""
    from mcp_server import main as _main
    nas = _main.nas
    if nas is None:
        return 0
    indexed = 0
    for pool_name in ("sata14", "sata15", "sata16", "sda1", "sdb1"):
        try:
            root = f"/{pool_name}/my/data/"
            indexed += await _dfs_scan_and_index_async(nas, root)
        except Exception:
            continue
    return indexed


async def _dfs_scan_and_index_async(nas, root: str) -> int:
    """DFS 扫目录,白名单文本文件 embed+入库。N150 限速 100ms/页。"""
    import time
    from .hooks import _is_text_file, _read_text_safe
    from .chunker import chunk_text
    from .store import insert_chunks_batch

    indexed = 0
    start = 0
    last_log = time.time()

    while True:
        try:
            resp = await nas.post("/v2/file/list", {
                "folderId": 0, "path": root, "start": start, "num": 200,
                "sortby": "name", "order": "asc", "show_hidden": 0,
            })
        except Exception as e:
            log.warning("list %s failed: %s", root, e)
            break
        if str(resp.get("code")) != "200":
            log.warning("list %s not 200: %s", root, resp.get("msg"))
            break
        items = (resp.get("data") or {}).get("list", [])
        if not items:
            break

        for item in items:
            # NAS 字段:is_dir="1" 表示目录(不是 type=="folder")
            if str(item.get("is_dir", "0")) == "1":
                child = root + item["name"] + "/"
                indexed += await _dfs_scan_and_index_async(nas, child)
                continue
            full_path = root + item["name"]
            if not _is_text_file(full_path):
                continue
            text = _read_text_safe(full_path)
            if not text:
                continue
            chunks = chunk_text(text)
            if not chunks:
                continue
            embs = embed_texts(chunks)
            items_for_db = [("file", full_path, snippet, vec) for snippet, vec in zip(chunks, embs)]
            n = insert_chunks_batch(items_for_db)
            indexed += 1
            if time.time() - last_log > 10:
                log.info("reindex progress: %d files indexed (last=%s)", indexed, full_path)
                last_log = time.time()

        if len(items) < 200:
            break
        start += 200
        await asyncio.sleep(0.1)  # N150 限速
    return indexed


async def _reindex_notebooks_async() -> int:
    """拉 location=2 笔记本,索引每个 body。"""
    from mcp_server import main as _main
    nas = _main.nas
    if nas is None:
        return 0

    try:
        resp = await nas.post("/v2/file/notepad/classifylist", {})
    except Exception as e:
        log.warning("notepad classifylist failed: %s", e)
        return 0
    classifies = (resp.get("data") or {}).get("list", []) if isinstance(resp, dict) else []
    if not classifies:
        classifies = [{"classify_id": 0}]

    from .chunker import chunk_text
    from .store import delete_chunks_by_source, insert_chunks_batch

    indexed = 0
    for c in classifies:
        cid = c.get("classify_id", 0)
        try:
            list_resp = await nas.post("/v2/file/notepad/list", {"classify_id": cid, "num": 200, "start": 0})
        except Exception:
            continue
        notes = (list_resp.get("data") or {}).get("list", []) if isinstance(list_resp, dict) else []
        for note in notes:
            note_id = note.get("id")
            title = note.get("title", "")
            try:
                info = await nas.post("/v2/file/notepad/info", {"id": note_id})
            except Exception:
                continue
            data = info.get("data") or {}
            body = data.get("body", "") or ""
            text = (title + "\n\n" + body).strip()
            if not text:
                continue
            path = f"notebook:{note_id}"
            delete_chunks_by_source("notebook", path)
            chunks = chunk_text(text)
            if not chunks:
                continue
            embs = embed_texts(chunks)
            items = [("notebook", path, snippet, vec) for snippet, vec in zip(chunks, embs)]
            insert_chunks_batch(items)
            indexed += 1
            if indexed % 10 == 0:
                log.info("reindex notebooks progress: %d indexed", indexed)
    return indexed


# 保留同步旧版以防外部 import(暂留空函数)
def _reindex_files() -> int:
    log.warning("_reindex_files 已废弃,改用 _reindex_files_async")
    return 0


def _reindex_via_mcp() -> int:
    return 0


def _dfs_scan_and_index(nas, root: str) -> int:
    return 0


def _reindex_notebooks() -> int:
    return 0


class _HooksAdapter:
    """适配 hooks.py 函数到 mcp_server/rag_hook.py 的字符串调用约定。"""
    def rag_on_file_write(self, resp, paths):
        rag_on_file_write(resp, paths)

    def rag_on_file_delete(self, resp, paths):
        rag_on_file_delete(resp, paths)

    def rag_on_file_move(self, resp, moves):
        rag_on_file_move(resp, moves)

    def rag_on_notebook_write(self, resp, payload):
        rag_on_notebook_write(resp, payload)

    def rag_on_notebook_delete(self, resp, ids):
        rag_on_notebook_delete(resp, ids)