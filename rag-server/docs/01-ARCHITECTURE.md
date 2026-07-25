# 01 - ARCHITECTURE

## 组件拆解

```
nas-rag-server/
├── app/
│   ├── server.py       ← FastAPI app + 路由
│   ├── embedder.py     ← bge-small-zh-v1.5 模型加载 + batch embed
│   ├── store.py        ← sqlite-vec 向量存储 + KNN
│   ├── chunker.py      ← 文本切片(500 字符重叠 50)
│   ├── scanner.py      ← DFS 扫 NAS 文件(N150 限速)
│   ├── hooks.py        ← /index /unindex 端点(主项目 MCP write tool 用)
│   ├── models.py       ← Pydantic 请求/响应 schema
│   └── config.py       ← 端口、路径、白名单常量
└── tests/
    ├── test_embedder.py
    ├── test_store.py
    └── test_api.py
```

## 数据流(详细)

### 1. /search 请求处理

```
POST /search {query: "一年级教材", scope: "files", top_k: 10}
    ↓
server.py: 路由分发
    ↓
embedder.embed_query("一年级教材")  →  [512 floats]   (~50ms)
    ↓
store.search(q_vec, scope="files", top_k=10)  →  [rows]
    ↓ KNN via sqlite-vec MATCH operator
server.py: 序列化响应
    ↓
HTTP 200 [{source_path, snippet, distance, ...}]
```

### 2. /reindex 请求处理

```
POST /reindex {scope: "files", full: true}
    ↓
server.py: 启动后台 task(返回 task_id 立即)
    ↓
scanner.dfs_scan(root="/sata14/my/data/")
    ↓
for path in scan:
    if is_text_file(path) and size <= 100KB:
        text = Path(path).read_text()
        chunks = chunker.chunk_text(text)
        embs = embedder.embed_texts(chunks)
        store.insert_chunks_batch([(file, path, c, v) for c, v in zip(chunks, embs)])
    ↓
后台进度打到 stdout(stderr 转发)
    ↓
完成(下次 /status 看 last_reindex)
```

### 3. /index /unindex(写时增量钩子,可选)

主项目 `mcp_server/tools/files.py` 改写工具(mkdir/copy/move/remove)后,异步调 NAS RAG daemon:
```
POST /index {source_type: "file", source_path: "/sata14/my/data/x.md", snippet: "..."}
POST /unindex {source_type: "file", source_path: "/sata14/my/data/x.md"}
```

daemon 内部:
- /index 读文件 → chunk → embed → store
- /unindex 按 source_path 删 chunks

**注意**:这是可选优化,Phase 1 不做。先做定期 reindex + /search。

## 关键技术点

### 1. embedder

```python
# app/embedder.py
from functools import lru_cache
from fastembed import TextEmbedding

MODEL_NAME = "BAAI/bge-small-zh-v1.5"  # 中文 SOTA 小模型

@lru_cache(maxsize=1)
def get_model() -> TextEmbedding:
    return TextEmbedding(model_name=MODEL_NAME)

def embed_query(query: str) -> list[float]:
    """单条 query 算 512 维向量,~50ms"""
    return list(get_model().embed([query]))[0].tolist()

def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量 embed,一次最多 32 条"""
    model = get_model()
    out = []
    for batch in _batched(texts, 32):
        for vec in model.embed(batch):
            out.append(vec.tolist())
    return out
```

### 2. store(sqlite-vec)

```sql
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,    -- 'file' / 'notebook'
    source_path TEXT NOT NULL,
    snippet TEXT NOT NULL,
    mtime INTEGER NOT NULL,
    UNIQUE(source_type, source_path, snippet)
);

CREATE VIRTUAL TABLE vec_index USING vec0(
    chunk_id INTEGER PRIMARY KEY,
    embedding float[512]          -- bge-small dim
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
```

KNN 查询(sqlite-vec 的 `MATCH` 操作符 + 隐藏 `distance` 列):
```sql
SELECT c.id, c.source_type, c.source_path, c.snippet, distance
FROM vec_index v
JOIN chunks c ON c.id = v.chunk_id
WHERE v.embedding MATCH ?
  AND k = ?
  [AND c.source_type = 'file']
ORDER BY distance
LIMIT ?
```

### 3. scanner(N150 限速)

```python
# app/scanner.py
SLEEP_BETWEEN_PAGES = 0.1   # 100ms 节流
PAGE_SIZE = 200

def dfs_scan(root):
    start = 0
    while True:
        items = Path(root).iterdir() if root_is_local else api_list(root)
        for item in items:
            if is_text(item) and item.size <= 100KB:
                # embed + store
                ...
        start += 200
        time.sleep(SLEEP_BETWEEN_PAGES)
```

实际不用 NAS API,**直接 Path.iterdir()**(服务跑在 NAS 上,文件就在本地)。

### 4. server(FastAPI)

```python
# app/server.py
from fastapi import FastAPI, Body
from pydantic import BaseModel

app = FastAPI(title="nas-rag-server")

class SearchReq(BaseModel):
    query: str
    scope: str = "all"  # 'all' / 'files' / 'notebooks'
    top_k: int = 10

@app.post("/search")
def search(req: SearchReq):
    q_vec = embed_query(req.query)
    return {"query": req.query, "scope": req.scope, "results": search(q_vec, req.scope, req.top_k)}
```

## 与主项目的数据交换

| 主项目(MCP client) | NAS daemon(RAG server) |
|---|---|
| `semantic_search(query)` tool | `POST /search {query, scope, top_k}` |
| `reindex(scope, full)` tool | `POST /reindex {scope, full}` |
| `index_status()` tool | `GET /status` |
| `hooks:rag_on_file_write/delete/move` | `POST /index` / `POST /unindex`(可选) |

**响应字段一致**(让主项目改最少代码)。

## 部署架构(NAS 上)

```
NAS (N150)
├── /zspace/zsrp/
│   ├── rag.db                 ← sqlite-vec 向量索引
│   ├── fastembed/             ← bge 模型 cache(从本机 sync)
│   └── docker-compose.yml      ← nas-rag-server 项目
├── /var/run/docker/...          ← nas-rag-server container(:8000)
└── /usr/openresty/...           ← 反代 8100 → 容器 :8000
    └── 8100_zspace_rag.conf
```

## 失败模式

| 场景 | 表现 | 处理 |
|---|---|---|
| daemon 挂 | 本机 MCP /search 失败 | docker restart policy |
| bge 模型丢 | embed_query 报错 | sync cache + 重启 |
| sqlite 损坏 | search 失败 | .db 自动备份(/zspace/zsrp/rag.db.bak) |
| NAS 文件权限 | scanner 跳过 | 不影响其他文件 |
| OOM(N150 内存小) | embed 失败 | docker 限制内存(--memory 1G),分批 embed |