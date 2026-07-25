# nas-rag-server

NAS 端 RAG 服务 — 接受 query,返回语义搜索结果。

```
┌─────────────────┐  HTTP POST /search     ┌──────────────────────────┐
│ 本机 MCP        │  {query, scope, top_k} │ NAS docker container      │
│ semantic_search ├───────────────────────→│ FastAPI + bge + sqlite-vec │
│ (消费,不 embed) │                        │  • 直接读 NAS 文件         │
│                 │←───────────────────────│  • NAS 本地 sqlite         │
└─────────────────┘   [{path, snippet}]   └──────────────────────────┘
```

## 跟主项目(zspace-mcp-poc)的关系

主项目里的 `mcp_server/tools/rag.py` 当前是**客户端 embed + 客户端 sqlite-vec**。这个设计有问题:NAS 文件不在客户端文件系统上,`Path.stat()` 永远失败,扫不到任何文件。

**修正后**:RAG 服务搬到 NAS(NAS 直接读自己的文件),本机 MCP 改成 HTTP 调用,纯消费。

代码从主项目 `rag/` 包复用,放到 `nas-rag-server/app/` 重新组织。

## 文件结构

```
nas-rag-server/
├── README.md             ← 你正在看的
├── docs/
│   ├── 00-OVERVIEW.md    ← 项目背景 + 架构图
│   ├── 01-ARCHITECTURE.md ← 组件拆解 + 数据流
│   ├── 02-DEPLOY.md       ← NAS docker 部署步骤
│   ├── 03-API.md          ← REST 协议详细
│   └── 04-TODO.md         ← 任务清单
├── Dockerfile            (Phase 2)
├── docker-compose.yml    (Phase 2)
├── requirements.txt      (Phase 2)
├── app/                  (Phase 2)
│   ├── server.py
│   ├── embedder.py
│   ├── store.py
│   ├── chunker.py
│   ├── scanner.py
│   ├── hooks.py
│   ├── models.py
│   └── config.py
├── tests/                (Phase 3)
└── scripts/              (Phase 2)
```

## 快速开始(部署到 NAS)

```bash
# 1. 同步模型(从已下载的本机 ~/.cache/fastembed 复制到 NAS)
sshpass -p "$KEY_SSH" scp -r ~/.cache/fastembed/ user@nas:/zspace/zsrp/fastembed/

# 2. 构建并启动
cd nas-rag-server
docker compose up -d

# 3. 反代到 NAS 主端口(可选,本机 MCP 直连也可以)
# 加 /usr/openresty/nginx/conf/vhost/8100_zspace_rag.conf,反代到 localhost:8000

# 4. 触发首次 reindex
curl -X POST http://nas:8100/reindex -d '{"scope": "files", "full": true}'
```

## 跟本机 MCP 的衔接(主项目侧)

主项目 `mcp_server/tools/rag.py` 改成只调 HTTP,不再本地 embed:

```python
@mcp.tool()
async def semantic_search(query: str, scope: str = "all", top_k: int = 10) -> str:
    """语义搜索 — 转发到 NAS RAG daemon"""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{NAS_RAG_URL}/search",
            json={"query": query, "scope": scope, "top_k": top_k},
            timeout=30,
        )
    return r.text
```

`NAS_RAG_URL` 从 .env 读,默认 `http://192.168.0.135:8100`。

## 关键决策

- **image 大小** ~500MB(bge 模型 ~100MB + python slim + 依赖 ~300MB + 代码 ~50KB)
- **CPU 推理** bge-small-zh-v1.5 ~50ms/查询,N150 够用(不需 GPU)
- **API 协议** 简单 REST,不学 OpenAI 协议(overkill)
- **存储** sqlite-vec 单文件,本地 NAS 文件系统(`/zspace/zsrp/rag.db`),git ignore

## 状态

Phase 1 文档设计,**代码未开始**。详见 [`docs/04-TODO.md`](docs/04-TODO.md)。