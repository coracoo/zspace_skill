---
name: smart-tagger
description: 自然语言搜 NAS 文件内容 → 批量打标签。Agent 调 RAG 语义检索(`semantic_search`)拿匹配文件,再用 MCP `save_file_label` 批量打标,用户弹 UI 批准。
  触发词:给 XX 内容的文件打 XX 标签、找所有 XX 给它们打标、给一年级的文件打《一年级》标签、按内容找文件打标、智能打标、语义打标、给所有 XX 内容的文件批量打标。
  不适用:按文件名/扩展名打标(用 label-manager)、按目录递归打标(用 label-manager 扫描)、单文件打标(直接 MCP save_file_label)、删除标签(用 label-manager)。
---

# Smart Tagger — RAG 语义搜 + 批量打标(案例三)

## 概述

NAS 里 1000+ 文件,用户用自然语言描述要找什么(比如"一年级的教材"),Agent 调 **RAG 语义检索**找到匹配文件,然后**批量打标签**。

这是真正盘活 NAS 文件管理的关键工作流 — 文件不再靠人工整理,而是 Agent 根据内容自动归类。

**关键能力依赖**:
- ✅ `semantic_search(query, scope, top_k)` MCP tool(RAG)
- ✅ `save_file_label(label_names, paths)` MCP tool(批量打标)
- ✅ `list_file_labels()` MCP tool(确认标签名)

**RAG 不可用时**:降级到 `list_files` + 文件名/扩展名匹配(走 label-manager 的 scan 命令)。

---

## 标准工作流

### 场景 1:用户用自然语言描述要找的文件 + 标签名

**用户说**:"给教材目录下所有一年级课本打《一年级》标签"

**步骤**:
1. **拆解需求**:
   - 内容关键词:`一年级课本 / 一年级教材 / 小学一年级`
   - 限定范围:`教材目录`(可推断路径,如 `/sata14/my/data/课程资料/`)
   - 标签名:`一年级`

2. **调 RAG 语义检索**(关键词组合,scope=files):
   ```
   semantic_search(query="一年级教材 一年级课本 小学一年级", scope="files", top_k=30)
   ```

3. **Agent 看返回的命中清单**:
   - `source_path` 列表(每个文件)
   - `snippet`(内容片段,Agent 判断真是一年级教材吗)
   - `distance`(相似度,<1.0 较相关,>1.2 弱)
   - **Agent 自己过滤**:剔除明显误报(distance 太大或 snippet 不对劲)

4. **确认标签名**(避免拼错):
   ```
   list_file_labels()  # 看 NAS 已有标签
   ```
   如果 `一年级` 已存在 → 直接用;不存在 → save_file_label 会自动创建

5. **批量打标**(MCP 客户端弹 UI 让用户批准):
   ```
   save_file_label(label_names="一年级", paths="path1,path2,path3,...")
   ```
   - **一次最多 50 个路径**(NAS 限制)
   - 多于 50 → 分批调

6. **报告**:返回"N 个文件已打《一年级》标签" + 示例路径前 5 个

### 场景 2:RAG daemon 没跑 / 不可用

**用户说**:"给所有 .pdf 的教材打 教材 标签"

**降级策略**(走 label-manager scan + 文件名匹配):
1. exec `python .claude/skills/label-manager/label_manager.py scan --root /sata14/my/data/课程资料/ --ext pdf --max-depth 5`
2. 读 scan 结果,Agent 过滤掉非教材(看文件名判断)
3. 调 `save_file_label("教材", "<paths>")`

**为什么降级**:RAG daemon 还在 Phase 2(nas-rag-server/),没真上线。Phase 5.1 完成前 smart-tagger 默认走降级路径。

### 场景 3:多标签场景

**用户说**:"给所有 docker 相关的文件,打 docker + 容器 两个标签"

**步骤**:
1. semantic_search(query="docker 容器 配置 镜像 compose", top_k=30)
2. Agent 过滤命中
3. save_file_label(label_names="docker,容器", paths="...") — 多标签逗号分隔,NAS 会都打上

### 场景 4:跨范围批量(目录 + RAG 双确认)

**用户说**:"给 /备份/2024 下所有发票打 发票 标签"

**步骤**:
1. **范围限定**:list_files(path="/sata14/my/data/备份/2024/") 拿到目录树
2. **RAG 找内容**:semantic_search(query="发票 invoice 报销单", top_k=30)
3. **Agent 取交集**:既在 /备份/2024/ 下,又 RAG 命中的
4. save_file_label("发票", "<交集 paths>")

---

## 关键约束(必读)

1. **写操作走 MCP** — `save_file_label` 永远让 MCP 客户端弹 UI 让用户批准,不要自动批量打(NAS 真落盘)
2. **一次最多 50 个路径** — NAS 限速,多于分批
3. **save_file_label 是覆盖式** — 打新标签前**先 `file_info(path)` 看当前标签**,避免覆盖掉已有标签
4. **RAG 命中要 Agent 二次判断** — distance < 1.0 较可靠,1.0-1.2 看 snippet,> 1.2 大概率误报
5. **scope=files 是文件内容**,不是文件名 — 文件名搜用 label-manager scan
6. **标签名先 list_file_labels 确认** — 避免拼错(比如 `docker` vs `Docker` 是两个标签)
7. **RAG daemon 没跑时降级** — 不要硬等,改走 label-manager scan + 文件名匹配
8. **N150 限速** — semantic_search 单次 ~50ms,save_file_label 每批串行不并发

---

## 工作流可视化

```
用户:"给一年级课本打《一年级》标签"
            ↓
    smart-tagger 触发
            ↓
   ┌──────────────────────────┐
   │ 1. 拆解:关键词 + 范围 + 标签 │
   └──────────────────────────┘
            ↓
   ┌──────────────────────────┐
   │ 2. semantic_search 找匹配 │
   │    query="一年级教材..."   │
   │    → [{path, snippet, d}] │
   └──────────────────────────┘
            ↓
   ┌──────────────────────────┐
   │ 3. Agent 过滤:            │
   │    - distance < 1.2       │
   │    - snippet 内容对得上    │
   │    - 在指定目录范围内      │
   └──────────────────────────┘
            ↓
   ┌──────────────────────────┐
   │ 4. list_file_labels()     │
   │    确认标签名 "一年级"     │
   └──────────────────────────┘
            ↓
   ┌──────────────────────────┐
   │ 5. save_file_label(        │ ← MCP 客户端弹 UI
   │    "一年级",               │   用户批准
   │    "path1,path2,...")     │
   └──────────────────────────┘
            ↓
   6. 报告:N 个文件已打标
```

---

## 跟其他 skill 的协作

| 场景 | 用哪个 skill |
|---|---|
| 按内容找文件 + 批量打标 | **smart-tagger**(本 skill) |
| 按文件名/扩展名找文件 + 批量打标 | `label-manager`(scan + save_file_label) |
| 反向查询:某标签下都有哪些文件 | `label-manager`(find-by-label) |
| 删除标签 | `label-manager`(delete_label + 二次确认) |
| 新建空标签 | `label-manager`(save_file_label + 占位 path) |
| 找重复/孤儿文件 | `file-organizer`(只读诊断) |

**核心分工**:
- `smart-tagger` = **按内容找**(RAG 语义检索)
- `label-manager` = **按元数据找**(文件名/扩展名/已有标签)
- 两个 skill 都用 `save_file_label` 做最终打标(MCP 弹 UI 批准)

---

## 已知 gap

- **RAG daemon 未上线**(Phase 2 待做) — 当前 smart-tagger 自动降级到 label-manager scan + 文件名匹配
- **RAG 召回率受文件类型限制** — 白名单 `.py/.md/.txt/.json/.yaml/.conf` 等纯文本;PDF/Word/图片不在
- **scope=notebooks 还没接** — Phase 6 才做笔记的 RAG
- **标签覆盖风险** — save_file_label 是覆盖式,建议先 file_info 看现有标签

---

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| `semantic_search` 超时 / 失败 | RAG daemon 没跑 | 降级走 label-manager scan |
| 命中 0 条 | RAG 没索引这个目录的文件 | 调 reindex(scope="files", full=true) 触发重扫 |
| 命中很多但 distance 都 > 1.2 | query 跟内容差太远 | 换关键词重试,或扩大 top_k |
| save_file_label 报 N001411 | path 不在 /池名/my/ 下 | 检查 path 格式 |
| save_file_label 报标签不存在 | (不应该) | save_file_label 自动创建新标签,不用预建 |
| 一批 50+ 个 path | NAS 限制 | 分批调,每批 ≤ 50 |

---

## 后续可以做(等需求)

- **多轮打标**:用户连续说"再给它们打 XX 标签",Agent 维护上下文
- **冲突检测**:打标前检查文件已有标签,提示"已有 Y 标签,要覆盖吗"
- **RAG 自动 reindex**:每次打标触发后台 reindex(等 Phase 6 写时增量钩子)
- **打标报告**:打完输出 markdown 报告(哪些打了/没打/为什么)