"""Pydantic 请求/响应 schema。"""
from pydantic import BaseModel, Field


class SearchReq(BaseModel):
    query: str = Field(..., min_length=1, description="自然语言 query")
    scope: str = Field("all", description="all / files / notebooks")
    top_k: int = Field(10, ge=1, le=50, description="返回数量,1-50")


class SearchHit(BaseModel):
    id: int
    source_type: str
    source_path: str
    snippet: str
    mtime: int
    distance: float


class SearchResp(BaseModel):
    query: str
    scope: str
    count: int
    results: list[SearchHit]


class ReindexReq(BaseModel):
    scope: str = Field("files", description="all / files / notebooks")
    full: bool = Field(False, description="true=清空重建,false=增量")


class ReindexResp(BaseModel):
    scope: str
    full: bool
    stats: dict
    total_chunks: int
    completed_at: str


class IndexReq(BaseModel):
    """单条索引(write hook 用)。"""
    source_type: str = Field(..., description="file / notebook")
    source_path: str = Field(...)
    file_content: str = Field(..., min_length=1)


class IndexResp(BaseModel):
    chunks_count: int


class UnindexReq(BaseModel):
    source_type: str
    source_path: str


class UnindexResp(BaseModel):
    removed: int


class StatusResp(BaseModel):
    model: str
    embed_dim: int
    total_chunks: int
    db_size_mb: float
    db_path: str
    last_reindex: str | None
    scope_stats: dict[str, int]