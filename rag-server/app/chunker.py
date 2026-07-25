"""文本切片 — 按字符数固定切片,保留重叠。

500 字符是中文常见的"段落级"大小,既保留语义又控制 embedding 维度噪声。
"""
from .config import CHUNK_OVERLAP, CHUNK_SIZE


def chunk_text(text: str) -> list[str]:
    """返回非空切片列表(按字符切片,不切词)。"""
    if not text or not text.strip():
        return []
    text = text.strip()
    if len(text) <= CHUNK_SIZE:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + CHUNK_SIZE, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = end - CHUNK_OVERLAP
    return chunks