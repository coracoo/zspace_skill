"""Embedding 模型(bge-small-zh-v1.5 via fastembed ONNX)。

模型首次加载自动下载到 ~/.cache/fastembed/,~100MB。后续启动秒加载。
"""
import logging
from functools import lru_cache
from typing import Sequence

from fastembed import TextEmbedding

from .paths import MODEL_NAME

log = logging.getLogger("zspace-rag")


@lru_cache(maxsize=1)
def get_model() -> TextEmbedding:
    log.info("loading embedder model %s (first call downloads ~100MB to ~/.cache/fastembed/)", MODEL_NAME)
    model = TextEmbedding(model_name=MODEL_NAME)
    log.info("embedder ready")
    return model


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """批量 embed。返回每个文本的 512 维向量(纯 Python list)。"""
    if not texts:
        return []
    model = get_model()
    out: list[list[float]] = []
    # fastembed 默认 max_length 限制;一次喂 batch,过大的列表也分批
    for batch in _batched(texts, 32):
        for vec in model.embed(batch):
            out.append([float(x) for x in vec])
    return out


def embed_query(query: str) -> list[float]:
    """单条查询 embed(语义搜索用)。"""
    return embed_texts([query])[0]


def _batched(seq, n):
    for i in range(0, len(seq), n):
        yield list(seq[i:i + n])