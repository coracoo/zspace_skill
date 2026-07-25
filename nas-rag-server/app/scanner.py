"""DFS 扫 NAS 文件系统,白名单文本文件 embed + 入库。

设计要点:
- 跑在 NAS 上,直接 Path.iterdir()/Path.read_text()(NAS 文件就在本地)
- N150 限速:每扫一批 sleep SLEEP_BETWEEN_PAGES(默认 100ms)
- 单文件 ≤ MAX_FILE_SIZE(默认 100KB),白名单扩展才索引
- 进度输出到 stderr,主项目 MCP tool 可以看
"""
import logging
import time
from pathlib import Path

from .chunker import chunk_text
from .config import (
    LIST_PAGE_SIZE,
    MAX_EMBED_FILES_PER_REINDEX,
    MAX_FILE_SIZE,
    SCAN_ROOTS,
    SLEEP_BETWEEN_PAGES,
    WHITELIST_EXTS,
)
from .embedder import embed_texts
from .store import insert_chunks_batch

log = logging.getLogger("nas-rag")


def is_text_file(path: Path) -> bool:
    """白名单扩展 + ≤ MAX_FILE_SIZE。"""
    if path.suffix.lower().lstrip(".") not in WHITELIST_EXTS:
        return False
    try:
        return path.is_file() and path.stat().st_size <= MAX_FILE_SIZE
    except OSError:
        return False


def read_text_safe(path: Path) -> str | None:
    """根据扩展名读文本:PDF 走 pypdf 提取,其他直接 read_text。

    PDF 限制:前 100 页 + 总文本 ≤ 200KB(避免大 PDF OOM,够 chunker 切 400+ chunks)。
    扫描版 PDF(图片型)extract_text 返回空,会被 caller 当无内容跳过。
    """
    if path.suffix.lower() == ".pdf":
        return _extract_pdf_text(path)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.debug("read failed %s: %s", path, e)
        return None


def _extract_pdf_text(
    path: Path,
    max_pages: int = 100,
    max_chars: int = 200_000,
) -> str | None:
    """pypdf 提取 PDF 文本。失败/扫描版返回 None。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("pypdf not installed, skip PDF: %s", path)
        return None
    try:
        reader = PdfReader(str(path))
        texts: list[str] = []
        total = 0
        for i, page in enumerate(reader.pages):
            if i >= max_pages:
                log.debug("pdf truncated at %d pages: %s", max_pages, path)
                break
            t = page.extract_text() or ""
            texts.append(t)
            total += len(t)
            if total >= max_chars:
                log.debug("pdf truncated at %d chars: %s", max_chars, path)
                break
        return "\n\n".join(texts) if texts else None
    except Exception as e:
        log.warning("pdf extract failed %s: %s", path, e)
        return None


def reindex_files() -> dict:
    """扫所有 SCAN_ROOTS,白名单文本文件 embed + 入库。返回统计。

    返回 {scanned_dirs, indexed_files, skipped_oversize, skipped_ext, errors}
    """
    stats = {
        "scanned_dirs": 0,
        "indexed_files": 0,
        "skipped_oversize": 0,
        "skipped_ext": 0,
        "errors": 0,
    }
    started = time.time()
    last_log = started

    for root in SCAN_ROOTS:
        root_path = Path(root)
        if not root_path.exists():
            log.warning("scan root missing: %s", root)
            continue
        log.info("scanning root: %s", root)
        stats["scanned_dirs"] += 1
        for indexed, over, ext_err in _walk_and_index(root_path, MAX_EMBED_FILES_PER_REINDEX):
            stats["indexed_files"] += indexed
            stats["skipped_oversize"] += over
            stats["skipped_ext"] += ext_err
            if time.time() - last_log > 10:
                log.info(
                    "reindex progress: indexed=%d oversize=%d ext_skip=%d (%.0fs)",
                    stats["indexed_files"], stats["skipped_oversize"],
                    stats["skipped_ext"], time.time() - started,
                )
                last_log = time.time()
    stats["elapsed_sec"] = round(time.time() - started, 1)
    return stats


def _walk_and_index(root: Path, max_files: int) -> tuple[int, int, int]:
    """DFS 一个目录树,yield (indexed, oversize_skip, ext_skip) 每 N 个文件。"""
    indexed = 0
    oversize = 0
    ext_skip = 0
    batch: list[tuple[str, str, str, list[float]]] = []
    batch_size = 0
    BATCH_FLUSH = 50  # 每 50 个文件 flush 一次入库

    # 用 os.walk(比 Path.rglob 快,且支持 dir error handling)
    import os
    for dirpath, dirnames, filenames in os.walk(root):
        # N150 限速:每个目录 sleep
        time.sleep(SLEEP_BETWEEN_PAGES)
        for fn in filenames:
            if indexed >= max_files:
                log.warning("max_files=%d reached, stop", max_files)
                return
            p = Path(dirpath) / fn
            try:
                if p.suffix.lower().lstrip(".") not in WHITELIST_EXTS:
                    ext_skip += 1
                    continue
                size = p.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_SIZE:
                oversize += 1
                continue
            text = read_text_safe(p)
            if not text:
                continue
            chunks = chunk_text(text)
            if not chunks:
                continue
            try:
                embs = embed_texts(chunks)
            except Exception as e:
                log.warning("embed failed for %s: %s", p, e)
                continue
            for snippet, vec in zip(chunks, embs):
                batch.append(("file", str(p), snippet, vec))
            indexed += 1
            batch_size += 1
            if batch_size >= BATCH_FLUSH:
                insert_chunks_batch(batch)
                batch.clear()
                batch_size = 0
                yield (0, 0, 0)  # 进度上报
                time.sleep(SLEEP_BETWEEN_PAGES)
    # flush 剩余
    if batch:
        insert_chunks_batch(batch)
    yield (indexed, oversize, ext_skip)