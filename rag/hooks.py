"""写时增量钩子(写 tool 完成后异步调用)。

hook 在 NAS 写入成功(code==200)时触发,索引/反索引文件内容。
异常全部吞掉,避免索引失败影响已成功的 NAS 写入。
"""
import logging
from pathlib import Path
from typing import Iterable

from .chunker import chunk_text
from .embedder import embed_texts
from .paths import MAX_FILE_SIZE, WHITELIST_EXTS
from .store import delete_chunks_by_source, insert_chunks_batch

log = logging.getLogger("zspace-rag")


def _is_text_file(path: str) -> bool:
    p = Path(path)
    if p.suffix.lower() not in WHITELIST_EXTS:
        return False
    try:
        return p.stat().st_size <= MAX_FILE_SIZE
    except OSError:
        return False


def _read_text_safe(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError as e:
        log.debug("read failed for %s: %s", path, e)
        return None


def _index_one_file(path: str) -> int:
    """索引 1 个文件(返回新增 chunk 数,重复不计)。"""
    if not _is_text_file(path):
        return 0
    text = _read_text_safe(path)
    if text is None:
        return 0
    chunks = chunk_text(text)
    if not chunks:
        return 0
    embs = embed_texts(chunks)
    items = [("file", path, snippet, vec) for snippet, vec in zip(chunks, embs)]
    n = insert_chunks_batch(items)
    log.info("indexed %d/%d chunks from %s", n, len(chunks), path)
    return n


def _unindex_one_file(path: str) -> int:
    n = delete_chunks_by_source("file", path)
    if n > 0:
        log.info("removed %d chunks for %s", n, path)
    return n


# 下面是 mcp_server 写工具调用的 4 个 hook

def rag_on_file_write(resp: dict, paths: Iterable[str]) -> None:
    """mkdir/copy 完成时调用,索引新文件。"""
    if str(resp.get("code")) != "200":
        return
    for path in paths:
        try:
            _index_one_file(path)
        except Exception as e:
            log.warning("index %s failed: %s", path, e)


def rag_on_file_delete(resp: dict, paths: Iterable[str]) -> None:
    """remove 完成时调用,反索引。"""
    if str(resp.get("code")) != "200":
        return
    for path in paths:
        try:
            _unindex_one_file(path)
        except Exception as e:
            log.warning("unindex %s failed: %s", path, e)


def rag_on_file_move(resp: dict, moves: Iterable[dict]) -> None:
    """move 完成时调用,moves 是 [{from:str, to:str}, ...]。"""
    if str(resp.get("code")) != "200":
        return
    for m in moves:
        src, dst = m.get("from"), m.get("to")
        if not src or not dst:
            continue
        try:
            _unindex_one_file(src)
            _index_one_file(dst)
        except Exception as e:
            log.warning("move-index %s -> %s failed: %s", src, dst, e)


def rag_on_notebook_write(resp: dict, payload: dict) -> None:
    """notebook_new/modify 调用,索引笔记 body。"""
    if str(resp.get("code")) != "200":
        return
    note_id = payload.get("id")
    title = payload.get("title", "")
    body = payload.get("body", "")
    if not body:
        return
    text = (title + "\n\n" + body).strip()
    path = f"notebook:{note_id}"
    # 删旧 chunks,重建
    try:
        _unindex_one_file(path)
        chunks = chunk_text(text)
        if not chunks:
            return
        embs = embed_texts(chunks)
        items = [("notebook", path, snippet, vec) for snippet, vec in zip(chunks, embs)]
        insert_chunks_batch(items)
    except Exception as e:
        log.warning("notebook index %s failed: %s", path, e)


def rag_on_notebook_delete(resp: dict, ids: Iterable) -> None:
    if str(resp.get("code")) != "200":
        return
    for note_id in ids:
        try:
            _unindex_one_file(f"notebook:{note_id}")
        except Exception as e:
            log.warning("notebook unindex %s failed: %s", note_id, e)