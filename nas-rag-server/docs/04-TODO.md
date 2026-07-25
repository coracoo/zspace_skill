# 04 - TODO

## Phase 1: 文档设计(本阶段 ✅)

- [x] README.md - 项目入口
- [x] docs/00-OVERVIEW.md - 背景 + 架构图
- [x] docs/01-ARCHITECTURE.md - 组件 + 数据流
- [x] docs/02-DEPLOY.md - NAS docker 部署步骤
- [x] docs/03-API.md - REST 协议详细
- [x] docs/04-TODO.md - 本文件

## Phase 2: 核心 MVP(下一步)

- [ ] `app/embedder.py` - bge-small-zh-v1.5 加载 + embed_query/embed_texts
- [ ] `app/store.py` - sqlite-vec + KNN 搜索(从主项目 rag/store.py 复用)
- [ ] `app/chunker.py` - 字符切片(从主项目 rag/chunker.py 复用)
- [ ] `app/scanner.py` - DFS 扫 NAS 文件(本地 Path,N150 限速 100ms)
- [ ] `app/models.py` - Pydantic schemas(SearchReq / IndexReq / StatusResp)
- [ ] `app/config.py` - 端口、路径、白名单常量
- [ ] `app/server.py` - FastAPI app + 路由
- [ ] `requirements.txt` - fastembed / sqlite-vec / onnxruntime / fastapi / uvicorn / pydantic
- [ ] `Dockerfile` - python:3.13-slim base + COPY 代码 + CMD uvicorn
- [ ] `docker-compose.yml` - 端口映射 + volumes + restart policy
- [ ] `.dockerignore` - 排除 docs/ tests/ scripts/

## Phase 3: 测试

- [ ] `tests/test_embedder.py` - 模型加载 + embed 一段中文 + 验证维度=512
- [ ] `tests/test_store.py` - insert + search + 删 chunk + 清表幂等
- [ ] `tests/test_api.py` - httpx AsyncClient + /search /status 端点测试
- [ ] 本机 docker compose up + smoke test(curl /search /status)

## Phase 4: NAS 端部署(等 SSH 恢复)

- [ ] 同步 fastembed cache 到 NAS(scp)
- [ ] scp nas-rag-server/ 到 NAS
- [ ] docker compose build + up
- [ ] 加 openresty 反代 8100_zspace_rag.conf
- [ ] systemd enable zspace-rag.service
- [ ] 触发首次 /reindex(scope="files", full=true)
- [ ] 验证 curl http://nas:8100/status

## Phase 5: 本机 MCP 改造(主项目)

- [ ] `mcp_server/tools/rag.py` 改成 HTTP 客户端:
  - [ ] `semantic_search(query, scope, top_k)` → POST NAS /search
  - [ ] `reindex(scope, full)` → POST NAS /reindex
  - [ ] `index_status()` → GET NAS /status
  - [ ] 删除本地 embedder/store/scanner 相关代码
- [ ] `.env.example` 加 `NAS_RAG_URL=http://192.168.0.135:8100`
- [ ] 测试 3 个 RAG tool 通过 MCP 调用 → NAS daemon → 命中
- [ ] 跟 label-manager 联动(semantic_search 找 → save_file_label 打标签)

## Phase 6: 优化(可选,看需求)

- [ ] 写时增量钩子(/index /unindex)+ 异步后台 task
- [ ] /reindex 异步化(返回 task_id,GET /reindex/status/{id} 查进度)
- [ ] sqlite backup(cron)
- [ ] 监控(daemon 健康检查 + daemon log 转发到 NAS 主日志)
- [ ] 笔记本 RAG(scope="notebooks")
- [ ] 多池扫描自动发现

## 不做(明确排除)

- ❌ 不学 OpenAI 协议(/v1/embeddings 等) — REST 简单够用
- ❌ 不加 LLM/RAG 问答 — 纯向量检索,无幻觉
- ❌ 不写分布式 — NAS 单实例
- ❌ 不做 UI — curl + 本机 MCP 客户端够用
- ❌ 不加鉴权 — LAN 信任
- ❌ 不做语义分块 — 字符切片够用,后续看效果

## 决策点(等用户确认)

- [ ] image 大小预算 ~500MB 是否 OK(更大的话换 base image)
- [ ] sqlite 路径 `/zspace/zsrp/rag.db` 是否 OK(可能要 `/zspace/zsrp/<userid>/rag.db` 按用户隔离)
- [ ] 是否需要 Phase 6 的写时增量钩子(还是只做 Phase 1-5 简化版)