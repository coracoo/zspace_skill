# 03 - API

REST 协议,JSON in/out,端口 8000(docker 内部)/ 8100(openresty 反代后)。

## POST /search

语义搜索 — 给一段 query 文本,返回 top_k 个命中。

**Request:**
```json
{
  "query": "一年级教材",
  "scope": "files",
  "top_k": 10
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| query | str | ✅ | 中文/英文自然语言 |
| scope | str |  | `all` / `files` / `notebooks`(默认 all) |
| top_k | int |  | 返回数量,默认 10,上限 50 |

**Response 200:**
```json
{
  "query": "一年级教材",
  "scope": "files",
  "count": 3,
  "results": [
    {
      "id": 42,
      "source_type": "file",
      "source_path": "/sata14/my/data/课程资料/人教版小学语文一年级上册.pdf",
      "snippet": "人教版小学一年级上册语文教材 包含拼音和课文...",
      "mtime": 1783863418,
      "distance": 0.732
    }
  ]
}
```

`distance` 越小越相关(L2 距离)。

## POST /reindex

触发全量(或增量)重建索引。**同步阻塞**(直接等)或**异步**(返回 task_id)。

**Request:**
```json
{
  "scope": "files",
  "full": true
}
```

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| scope | str | all | `all` / `files` / `notebooks` |
| full | bool | false | `true` = 清空重建,`false` = 只索引 hash 没命中的(暂未实现,等同 full) |

**Response 200(同步):**
```json
{
  "scope": "files",
  "full": true,
  "indexed_files": 1234,
  "total_chunks": 5678,
  "elapsed_sec": 1234,
  "completed_at": "2026-07-25T03:45:00"
}
```

**Response 202(异步 task_id):** 待 Phase 3 实现。

## POST /index

**单条**索引(写时增量钩子用)。主项目 MCP 写工具完成后调。

**Request:**
```json
{
  "source_type": "file",
  "source_path": "/sata14/my/data/x.md",
  "file_content": "...文本内容..."
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| source_type | str | ✅ | `file` / `notebook` |
| source_path | str | ✅ | 唯一标识(同类型内) |
| file_content | str | ✅ | 文本内容(≤100KB) |

daemon 内部:chunk → embed → store.upsert(先删同 source_path 的旧 chunks)

**Response 200:**
```json
{"chunk_id": 42, "chunks_count": 3}
```

## POST /unindex

反索引。删除某 source_path 的所有 chunks。

**Request:**
```json
{
  "source_type": "file",
  "source_path": "/sata14/my/data/x.md"
}
```

**Response 200:**
```json
{"removed": 3}
```

## GET /status

索引概况。

**Response 200:**
```json
{
  "model": "BAAI/bge-small-zh-v1.5",
  "embed_dim": 512,
  "total_chunks": 5678,
  "db_size_mb": 12.4,
  "db_path": "/app/data/rag.db",
  "last_reindex": "2026-07-25T03:45:00",
  "scope_stats": {
    "file": 4521,
    "notebook": 1157
  }
}
```

## 错误码

| HTTP code | 含义 | 触发场景 |
|---|---|---|
| 200 | OK | 成功 |
| 400 | Bad Request | query 字段缺失、scope 取值非法 |
| 422 | Unprocessable Entity | Pydantic 验证失败 |
| 500 | Internal Server Error | bge 模型没找到、sqlite 损坏、NAS 文件读失败 |
| 503 | Service Unavailable | 还没 init_db(daemon 启动初期) |

**Error response:**
```json
{"error": "embed_query failed: model not loaded"}
```

## CORS / 鉴权

- **不启用 CORS**(只接受 NAS 网内调用,无浏览器跨域需求)
- **不启用鉴权**(假设 LAN 信任)
- 如要加 API key,加 `X-API-Key` header 检查

## 限流

不限流(N150 性能受限,embed 一次 ~50ms,自然限速)。

如要防滥用,加 nginx rate limit:
```nginx
limit_req_zone $binary_remote_addr zone=rag:10m rate=10r/s;
location / {
    limit_req zone=rag burst=20 nodelay;
    proxy_pass http://127.0.0.1:8000;
}
```

## 健康检查

```bash
curl http://nas:8100/status
# 200 OK + 包含 total_chunks > 0 表示健康
```

Docker healthcheck(在 docker-compose.yml):
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/status"]
  interval: 60s
  timeout: 10s
  retries: 3
```

## 性能预期(N150)

| 操作 | 耗时 |
|---|---|
| `/search` 单查询 | ~50ms(embed 50ms + KNN < 5ms) |
| `/reindex` 1000 文件 | ~5 分钟(N150 限速 100ms/请求 + embed ~50ms/文本) |
| `/index` 单文件 50KB | ~100ms |
| `/status` | < 10ms |

## 客户端示例(Python)

```python
import httpx

class NASRAGClient:
    def __init__(self, base_url="http://nas:8100"):
        self.base_url = base_url
    
    async def search(self, query, scope="all", top_k=10):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.base_url}/search",
                            json={"query": query, "scope": scope, "top_k": top_k})
            r.raise_for_status()
            return r.json()
    
    async def reindex(self, scope="files", full=True):
        async with httpx.AsyncClient(timeout=3600) as c:
            r = await c.post(f"{self.base_url}/reindex",
                            json={"scope": scope, "full": full})
            r.raise_for_status()
            return r.json()
    
    async def status(self):
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{self.base_url}/status")
            r.raise_for_status()
            return r.json()
```