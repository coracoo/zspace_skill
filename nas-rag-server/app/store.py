"""向量存储:sqlite-vec + sqlite3。

表结构:
- chunks(id, source_type, source_path, snippet, mtime)
- vec_index(chunk_id, embedding FLOAT[512])  — sqlite-vec 虚拟表
- meta(key, value)  — 模型名/最后 reindex 时间

KNN 查询用 sqlite-vec 的 `MATCH` 操作符 + 隐藏 `distance` 列。
"""
import logging
import sqlite3
import struct
from datetime import datetime
from pathlib import Path
from typing import Optional

import sqlite_vec

from .config import DB_PATH, EMBED_DIM

log = logging.getLogger("nas-rag")


def get_conn() -> sqlite3.Connection:
    """每个 conn 单独 load sqlite-vec 扩展(进程内扩展,但每个连接独立注册)。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    return conn


def init_db() -> None:
    """首次跑时建表(server 启动前调)。"""
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source_path TEXT NOT NULL,
                snippet TEXT NOT NULL,
                mtime INTEGER NOT NULL,
                UNIQUE(source_type, source_path, snippet)
            )
        """)
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_index USING vec0(
                chunk_id INTEGER PRIMARY KEY,
                embedding float[{EMBED_DIM}]
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # 索引(加速反查 / unindex / scope 过滤)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_type, source_path)"
        )
        conn.commit()
        log.info("rag db initialized at %s", DB_PATH)
    finally:
        conn.close()


def count_chunks() -> int:
    conn = get_conn()
    try:
        cur = conn.execute("SELECT COUNT(*) FROM chunks")
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def count_chunks_by_scope() -> dict[str, int]:
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT source_type, COUNT(*) FROM chunks GROUP BY source_type"
        )
        return {row[0]: int(row[1]) for row in cur.fetchall()}
    finally:
        conn.close()


def delete_chunks_by_source(source_type: str, source_path: str) -> int:
    """删某 source 的所有 chunks(返回删了几条)。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT id FROM chunks WHERE source_type=? AND source_path=?",
            (source_type, source_path),
        )
        ids = [r[0] for r in cur.fetchall()]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM vec_index WHERE chunk_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM chunks WHERE id IN ({placeholders})", ids)
        conn.commit()
        return len(ids)
    finally:
        conn.close()


def insert_chunks_batch(items: list[tuple[str, str, str, list[float]]]) -> int:
    """批量入库。

    items: [(source_type, source_path, snippet, embedding), ...]
    返回新增数(UNIQUE 冲突的不计)。
    """
    if not items:
        return 0
    conn = get_conn()
    try:
        n = 0
        mtime = int(datetime.now().timestamp())
        for source_type, source_path, snippet, embedding in items:
            cur = conn.execute(
                "INSERT OR IGNORE INTO chunks (source_type, source_path, snippet, mtime) VALUES (?, ?, ?, ?)",
                (source_type, source_path, snippet, mtime),
            )
            if cur.lastrowid == 0:
                continue
            chunk_id = cur.lastrowid
            vec_bytes = serialize_vector(embedding)
            conn.execute(
                "INSERT INTO vec_index (chunk_id, embedding) VALUES (?, ?)",
                (chunk_id, vec_bytes),
            )
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def search(query_embedding: list[float], scope: str = "all", top_k: int = 10) -> list[dict]:
    """KNN 搜索。scope: all / files / notebooks(过滤 source_type)。"""
    conn = get_conn()
    try:
        if scope == "files":
            where = "AND c.source_type = 'file'"
        elif scope == "notebooks":
            where = "AND c.source_type = 'notebook'"
        else:
            where = ""

        vec_bytes = serialize_vector(query_embedding)
        sql = f"""
            SELECT c.id, c.source_type, c.source_path, c.snippet, c.mtime, distance
            FROM vec_index v
            JOIN chunks c ON c.id = v.chunk_id
            WHERE v.embedding MATCH ?
              AND k = ?
              {where}
            ORDER BY distance
        """
        cur = conn.execute(sql, (vec_bytes, top_k))
        return [
            {
                "id": r["id"],
                "source_type": r["source_type"],
                "source_path": r["source_path"],
                "snippet": r["snippet"][:200] + ("…" if len(r["snippet"]) > 200 else ""),
                "mtime": r["mtime"],
                "distance": round(float(r["distance"]), 4),
            }
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def serialize_vector(vec: list[float]) -> bytes:
    """512 维 float list → sqlite-vec bytes。"""
    if len(vec) != EMBED_DIM:
        raise ValueError(f"vector dim mismatch: got {len(vec)}, expected {EMBED_DIM}")
    return struct.pack(f"{len(vec)}f", *vec)


def set_meta(key: str, value: str) -> None:
    conn = get_conn()
    try:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def get_meta(key: str) -> Optional[str]:
    conn = get_conn()
    try:
        cur = conn.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def clear_scope(source_type: Optional[str] = None) -> int:
    """清空某 scope(all/file/notebook)的所有 chunks + vec。返回删了几条。"""
    conn = get_conn()
    try:
        if source_type is None:
            cur = conn.execute("SELECT COUNT(*) FROM chunks")
            total = int(cur.fetchone()[0])
            conn.execute("DELETE FROM vec_index")
            conn.execute("DELETE FROM chunks")
        else:
            cur = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE source_type=?", (source_type,)
            )
            total = int(cur.fetchone()[0])
            conn.execute(
                "DELETE FROM vec_index WHERE chunk_id IN "
                "(SELECT id FROM chunks WHERE source_type=?)",
                (source_type,),
            )
            conn.execute("DELETE FROM chunks WHERE source_type=?", (source_type,))
        conn.commit()
        return total
    finally:
        conn.close()