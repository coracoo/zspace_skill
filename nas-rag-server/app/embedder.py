"""Embedding 模型(bge-small-zh-v1.5 via fastembed ONNX)。

模型 cache 走 HF_HOME 环境变量(config.py 已设置),首次加载下载 ~100MB 到
$FASTEMBED_CACHE_DIR,后续秒加载。
"""
import logging
from functools import lru_cache
from typing import Sequence

from fastembed import TextEmbedding

from .config import MODEL_NAME

log = logging.getLogger("nas-rag")


@lru_cache(maxsize=1)
def get_model() -> TextEmbedding:
    """单例 TextEmbedding。lru_cache 保证全局只加载一次。"""
    log.info("loading embedder model %s (first call downloads ~100MB)", MODEL_NAME)
    model = TextEmbedding(model_name=MODEL_NAME)
    log.info("embedder ready")
    return model


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """批量 embed。返回每个文本的 512 维向量(纯 Python list[float])。

    fastembed 内部分批(默认 max_length 4096 tokens / batch),不需要外面再切。
    """
    if not texts:
        return []
    model = get_model()
    out: list[list[float]] = []
    # 喂 32 条/批,避免单批太大 OOM(N150 内存敏感)
    for batch in _batched(texts, 32):
        for vec in model.embed(batch):
            out.append([float(x) for x in vec])
    return out


def embed_query(query: str) -> list[float]:
    """单条 query embed(语义搜索用)。"""
    return embed_texts([query])[0]


def _batched(seq, n):
    for i in range(0, len(seq), n):
        yield list(seq[i:i + n])