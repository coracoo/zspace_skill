---
name: rag-manager
description: Use when 需要管理 RAG 语义搜索的索引生命周期 — 检查索引状态、重建索引、增量索引、移除索引、配置扫描范围。smart-tagger 和其他 RAG 依赖 skill 的前置门控。
  触发词:RAG 索引、语义搜索索引、重建索引、索引状态、重新索引、更新索引、reindex、NAS 文件索引、搜索索引管理、索引文件更新。
  不适用:语义搜索本身(用 MCP semantic_search tool)、文件打标签(用 label-manager / smart-tagger)、文件审计(用 file-organizer)。
---

# RAG Manager — 语义搜索索引管理

## 概述

管理 NAS 上 RAG docker daemon(`<nas_ip>:8000`,bge-small-zh-v1.5 + sqlite-vec)的索引生命周期。

**前置**:`nas-setup` skill(验证 NAS 登录 + RAG daemon 在线)。

**依赖 MCP tool**:
- `index_status()` — 索引概况(model/chunks/db_size)
- `reindex(scope, full)` — 重建索引
- `semantic_search(query, scope, top_k)` — 按 query 搜索(验证索引用)

RAG **不会**自动扫描 NAS 文件。需要手动 `reindex` 或 NAS cron 定时触发。

---

## 工作流

### 场景 1:门控检查(smart-tagger 等依赖 skill 的前置)

**触发**:smart-tagger 或任何 RAG 依赖 skill 被加载时,先调 rag-manager 做门控。

**步骤**:
1. 调 `index_status()` → 拿 `chunks`、`model`、`db_size`
2. 如果 `chunks == 0`:调 `reindex(scope="files", full=true)` → 等完成后重查 status
3. 如果 `error` 或 daemon 不通:返回错误,"RAG daemon 未就绪,请先部署 rag-server docker"
4. 如果 `chunks > 0`:门控通过,告诉调用方"RAG ready,chunks=X"

### 场景 2:用户说"更新 RAG 索引"

**触发**:"重建 RAG 索引"、"reindex"、"把 NAS 文件重新索引"、"RAG 搜不到新文件"

**步骤**:
1. 调 `index_status()` 看当前状态
2. 调 `reindex(scope="files", full=true)` → 同步阻塞(大目录几分钟)
3. 调 `index_status()` 确认 chunks 数量增加
4. 报告:已索引 X chunks,共 Y MB

### 场景 3:用户说"去掉某个文件的索引"

**触发**:"把 XX 文件从 RAG 索引里移除"、"unindex"

**步骤**:
1. 当前 MCP **无 `unindex` tool**。走 rag-server HTTP API:`POST /unindex {source_type, source_path}`
2. 确认状态 `/status` → chunks 减少

### 场景 4:用户说"配置 RAG 扫描范围"

**触发**:"RAG 只索引教材目录"、"让 RAG 扫更多目录"

**步骤**:
1. 编辑 `rag-server/.env` 的 `SCAN_ROOTS` 和 `NAS_RAG_PATH_MAP`
2. 调 `reindex(scope="files", full=true)` 应用新范围
3. API 不暴露修改配置端点(需 SSH 或 docker compose restart)

---

## 跟 smart-tagger 的分工

| 能力 | 用哪个 |
|------|--------|
| 索引状态门控 | rag-manager |
| 重建索引 | rag-manager |
| 内容语义搜索 | smart-tagger |
| 搜索结果打标签 | smart-tagger → MCP save_file_label |
| 文件内容浏览 | smart-tagger |

smart-tagger 场景 1 步骤 2 改为:**加载 rag-manager skill → 门控检查通过 → 继续**。

---

## 关键约束

- `reindex` 是同步阻塞的(大目录几分钟),期间 MCP 会一直等待,LLM 应提示用户耐心等
- `SCAN_ROOTS` 在 `rag-server/.env` 里,修改需要容器重启:`docker compose restart`
- RAG daemon 部署在 NAS docker 上(不是本地),`/status` 优先走 `http://<nas_ip>:8000/status`
- 索引数据在 `rag-server/data/`(sqlite-vec),体积随文件数线性增长
