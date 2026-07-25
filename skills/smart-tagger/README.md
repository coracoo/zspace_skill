# Smart Tagger Skill

NAS 文件**内容**语义搜 → 批量打标签(RAG + label 联动)。

## 何时用

- 用户用**自然语言**描述要找什么(不靠文件名)
- 然后批量打标签

例:"给教材目录下所有一年级课本打《一年级》标签"

## 快速使用

无需脚本 — Agent 看到 SKILL.md 触发词自动加载,流程:

```
semantic_search(query="一年级教材 ...", scope="files", top_k=30)
  → 拿命中文件列表
list_file_labels()  → 确认标签名
save_file_label(label_names="一年级", paths="...")  → MCP 弹 UI 让用户批准
```

## 依赖

- MCP tool:`semantic_search`(RAG)、`save_file_label`、`list_file_labels`
- RAG daemon:`rag-server/`(Phase 2 待做) — 没跑时降级走 `label-manager scan`

## 跟 label-manager 的分工

| 找文件方式 | 用哪个 |
|---|---|
| **按内容**(语义) | smart-tagger |
| **按文件名/扩展名** | label-manager scan |

详见 [`SKILL.md`](SKILL.md)。