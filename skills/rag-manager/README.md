# RAG Manager Skill

管理 NAS RAG 语义搜索索引。纯 LLM 编排,无 Python 脚本。

## 快速使用

无脚本 — Agent 看到 SKILL.md 触发词自动加载,调 MCP tool 完成:

```
index_status()  → 门控
reindex(scope, full)  → 重建索引
```

## 依赖

- MCP tool:`index_status`、`reindex`、`semantic_search`
- RAG daemon:`rag-server/` docker 部署在 NAS 上

## 触发词

从 SKILL.md frontmatter 加载。

详见 [`SKILL.md`](SKILL.md)。