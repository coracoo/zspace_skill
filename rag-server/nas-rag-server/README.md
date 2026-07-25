# nas-rag-server

NAS docker RAG 服务 — bge-small-zh-v1.5 + sqlite-vec + FastAPI。

```
本机 MCP semantic_search     HTTP POST /search        nas-rag docker container
(消费,不 embed)          ←→ {query, scope, top_k}  ←→ FastAPI + bge + sqlite-vec
                                                      直接读 NAS 文件(NAS 文件系统挂载)
```

## 快速部署

```bash
docker compose up -d                                    # image: coracoo/cherry:nas_rag
curl http://localhost:8000/status                       # 验证
curl -X POST http://localhost:8000/reindex \
  -H 'Content-Type: application/json' \
  -d '{"scope":"files","full":true}'                    # 首次建索引
```

## 端点

| Method | Path | 说明 |
|---|---|---|
| `GET` | `/status` | 索引概况(model/chunks/db_size) |
| `GET` | `/healthz` | 健康检查 |
| `POST` | `/search` | 语义搜索 `{query, scope, top_k}` |
| `POST` | `/reindex` | 重建索引 `{scope, full}` |
| `POST` | `/index` | 单条索引(写时增量) `{source_type, source_path, file_content}` |
| `POST` | `/unindex` | 反索引 `{source_type, source_path}` |

REST 协议详细见 [docs/03-API.md](docs/03-API.md)。

## 配置(环境变量)

| 变量 | 默认 | 说明 |
|---|---|---|
| `RAG_DB_PATH` | `/app/data/rag.db` | sqlite 数据文件 |
| `FASTEMBED_CACHE_DIR` | `/root/.cache/fastembed` | bge 模型 cache |
| `HF_HUB_OFFLINE` | 1 | 离线模式(不走网络) |
| `RAG_SCAN_ROOTS` | `/nas_data/` | 扫描根目录 |
| `RAG_WHITELIST_EXTS` | .py,.pdf,.md,.txt,.json,.yaml,.yml,.conf | 文件白名单 |
| `RAG_MAX_FILE_SIZE_KB` | 102400 | 单文件上限(KB) |
| `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` | 500 / 50 | 切片参数 |
| `RAG_SLEEP_BETWEEN_PAGES` | 100 | N150 限速(ms) |

## 文件结构

```
nas-rag-server/
├── app/
│   ├── server.py    FastAPI 5 端点
│   ├── embedder.py  bge-small-zh-v1.5 模型
│   ├── store.py     sqlite-vec KNN
│   ├── scanner.py   DFS + pypdf PDF 提取
│   ├── chunker.py   文本切片
│   ├── models.py    Pydantic schemas
│   └── config.py    配置常量
├── Dockerfile + docker-compose.yml
├── requirements.txt
└── docs/03-API.md   REST 协议详细
```
