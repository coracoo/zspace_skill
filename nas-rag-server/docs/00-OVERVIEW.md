# 00 - OVERVIEW

## 为什么需要独立 RAG 服务

### 现状(主项目)
- `mcp_server/tools/rag.py` 在**本机**跑 RAG(bge embed + sqlite-vec)
- 本机 Path.stat() / Path.open() 试图访问 `/sata14/my/data/...` —— **永远失败**,因为 NAS 文件不在本机文件系统上
- 结果:reindex 跑了 1+ 小时,索引 0 个文件

### 修正后
- RAG 服务**部署到 NAS 本身**(docker container,NAS 直接读自己的文件)
- 本机 MCP 改成 HTTP 客户端,**纯消费不生产**
- 不依赖 NFS / samba 挂载

### 跟主项目其他部分的关系

| 组件 | 位置 | 角色 |
|---|---|---|
| `mcp_server/tools/rag.py` | 主项目 | 客户端(改成 HTTP 调用) |
| `mcp_server/tools/files.py` | 主项目 | 不变(NAS 文件操作) |
| `nas-rag-server/` | **独立文件夹** | 服务端(NAS 上跑) |
| `nas-rag-server/app/` | NAS 端 | FastAPI + bge + sqlite-vec |
| `nas-rag-server/Dockerfile` | NAS 端 | 打包运行环境 |

### 迁移性

整个 `nas-rag-server/` 是**自包含的**:
- 不依赖主项目的 nas/ mcp_server/ nas_client.py
- 有自己的 embedder / store / scanner(从主项目 rag/ 复制并重组)
- 任意一台 Linux + Docker 都能跑(N150、其他 NAS、x86 都能)

如果以后想从极空间 NAS 迁到 Synology / unRAID,只搬 `nas-rag-server/` + NAS sqlite 数据库即可,不动主项目。

## 数据流

```
                    POST /search {"query": "一年级教材"}
                    ↓
┌──────────────────┐   ┌─────────────────────────────────────┐
│ 本机 MCP         │   │ NAS docker container                │
│                  │   │                                     │
│ semantic_search  │←→│ FastAPI                              │
│ ↓                │   │  ↓                                  │
│ httpx POST       │   │  embed_query("一年级教材")          │
│                 │   │   ↓ bge-small-zh-v1.5 算 512 维    │
│                 │   │  search(q_vec, scope, top_k)        │
│                 │   │   ↓ sqlite-vec KNN                  │
│                 │   │  [{source_path, snippet, distance}] │
└──────────────────┘   └─────────────────────────────────────┘
                        NAS 本地 sqlite (/zspace/zsrp/rag.db)
                        bge 模型 cache (~/.cache/fastembed/,复制过来)
```

## 关键边界

| 边界 | 一侧 | 另一侧 |
|---|---|---|
| **RAG 服务边界** | NAS daemon(embed + KNN) | 本机 MCP(HTTP 客户端) |
| **NAS 文件访问** | NAS daemon(Path.read 本地) | 不存在(NAS 没暴露文件内容 HTTP 端点) |
| **NAS sqlite** | NAS daemon 写 | 不存在(本机有自己的 ~/.cache/zspace-rag/) |
| **bge 模型** | NAS daemon(从本机 ~/.cache/fastembed 同步) | 本机可能也有(主项目用过) |

## 什么时候触发 reindex

| 场景 | 谁触发 | 频率 |
|---|---|---|
| 首次部署 | 手动(NAS UI 按钮或 curl) | 一次 |
| 新增/删除/修改文件 | NAS 端**不自动**(避免每次写都跑 embed) | 手动定期 |
| 内容被 NAS 端外部改 | 手动(SSH 上 daemon,跑 POST /reindex) | 按需 |
| 定时 | NAS cron(每天凌晨) | 1 次/天 |

写时**增量**钩子(每 mkdir/copy/move 都触发 embed)复杂且收益低,**不做**。改为定期全量 reindex,简单可靠。