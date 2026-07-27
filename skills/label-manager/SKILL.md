---
name: label-manager
description: 管理和查询 ZSpace NAS 标签(打标、批量打标、按标签找文件、新建/删除标签)。
  触发词:打标签、给 XX 打 XX 标签、按标签找、找所有带 XX 标签的文件、新建标签、删除标签。
  不适用:文件重命名(用 MCP file_rename)、笔记搜索(用 MCP notebook_search)、文件内容搜索。
---

# Label Manager — NAS 标签管理工作流

## 概述

通过组合 NAS MCP tool + `label_manager.py` 脚本,完成标签的**全生命周期**管理:

| MCP tool(原子) | 用途 |
|----------------|------|
| `list_file_labels` | 列出所有标签 |
| `save_file_label(label_names, paths)` | 打标签(覆盖式,**会自动创建新标签名**) |
| `delete_label(label_names)` | 删除标签(从所有文件上**彻底移除**) |
| `notebook_updatelabel(id, label)` | 笔记标签 |

| label_manager.py 命令(机械活) | 用途 |
|-------------------------------|------|
| `list-labels` | 同 MCP,但走脚本(批量友好) |
| `scan --root X --ext Y` | BFS 扫目录找文件(LLM 决策前先用) |
| `find-by-label --label X` | 反向查询带某标签的文件 |

---

## 5 个标准场景

### 场景 1:给单个/多个文件打标签

**用户说**:"给 `/sata14/my/data/docker-compose.yml` 打 docker 标签"

**步骤**:
1. 调 `save_file_label(label_names="docker", paths="/sata14/my/data/docker-compose.yml")`
2. 返回结果(返回 200 即可)

**注意**:
- `paths` 多个用英文逗号分隔,最多 50 个/次(NAS 限速)
- 如果 `label_names` 里有**不存在的标签名**,NAS 会**自动创建**

### 场景 2:批量打标(目录递归 + LLM 决策)

**用户说**:"把 `/sata14/my/data/` 下所有 .yml 文件打 docker 标签"

**步骤**:
1. **exec** `python skills/label-manager/label_manager.py scan --root /sata14/my/data/ --ext yml --max-depth 5 --output /tmp/scan.json`
2. 读 `/tmp/scan.json`,得到 `items` 数组(含 path/name/labels)
3. **LLM 自己判断**哪些真该打(README.yml 不该打),过滤后分批(每批 50 个)
4. LLM 调 `save_file_label(label_names="docker", paths="path1,path2,...")` 分批执行

**为什么 LLM 决策**:不是所有 .yml 都是 docker 配置。LLM 看 path/name 决定。

### 场景 3:按标签找文件(反向查询)

**用户说**:"找所有带 docker 标签的文件"

**步骤**:
1. **exec** `python skills/label-manager/label_manager.py find-by-label --label docker --root /sata14/my/data/ --max-depth 5 --output /tmp/docker.json`
2. 读 `/tmp/docker.json`,得到 `matches` 数组(含 path/name/is_dir/labels)
3. 格式化返回给用户

**已知 gap**:
- 受 `--max-depth` 限制,深度外文件找不到
- 用户只能扫 `/<pool>/my/<子目录>/`,跨池越权 N001411
- 脚本内部走 BFS + 串行,sleep 0.1s/层,100 个目录约 10s

### 场景 4:新建标签

**用户说**:"新建一个 备份 标签"

**步骤**:
1. 调 `save_file_label(label_names="备份", paths="/sata14/my/data/")`(自动建)
2. 可选:调 `list_file_labels()` 验证已存在

**注意**:
- NAS **没有专门的"创建标签"端点** — 用 `save_file_label` 传不存在标签名会自动建
- 想纯创建不打任何文件:传 `paths="/sata14/my/data/"`(任意已有路径)

### 场景 5:删除标签(⚠️ 必须二次确认)

**用户说**:"删除 docker 标签"

**步骤**:
1. **必须先**调 `list_file_labels()` 确认标签名拼写正确(避免删错)
2. **LLM 必须显式二次确认**:"即将删除 'docker' 标签,这会让所有文件上的 'docker' 标签消失,确认吗?"
3. 用户确认后,调 `delete_label(label_names="docker")`
4. 警告:该标签会从**所有文件**彻底移除,不可恢复

---

## 关键约束(必读)

1. **串行不并发**:N150 性能差,任何批量操作串行,每步 sleep 0.1s
2. **判断交回 LLM**:脚本只做机械活(扫文件、过滤),决策(打哪些、删哪些)由 LLM 判断
3. **删除前必须确认**:误删标签会让所有文件上的该标签消失
4. **路径格式**:`/<pool>/my/<子目录>/`,**目录必须以 `/` 结尾**
5. **save_file_label 是覆盖式**:会清掉文件已有的其他标签。打之前先 `file_info(path)` 看当前标签
6. **打标签前先 list_file_labels**:确认标签名,避免拼错
7. **写操作走 MCP tool**:不要在脚本里加 apply / delete 子命令(LLM 弹 UI 让用户批更好)

---

## 调用示例

### 例 1:用户说"找 docker-compose.yml,打 docker 标签"
- LLM:调 `list_files("/sata14/my/data/")` → 找到路径 `/sata14/my/data/docker-compose.yml`
- LLM:调 `save_file_label("docker", "/sata14/my/data/docker-compose.yml")`
- 回复:"已打标"

### 例 2:用户说"把 /sata14/my/data/ 下所有 .yml 打 docker"
- LLM:`exec label_manager.py scan --root /sata14/my/data/ --ext yml --max-depth 5`
- LLM:读 candidates,**过滤**掉 README/CHANGELOG 等
- LLM:批量调 `save_file_label`(每批 50 个)

### 例 3:用户说"找所有 docker 标签的文件"
- LLM:`exec label_manager.py find-by-label --label docker`
- LLM:读结果,格式化返回

### 例 4:用户说"新建一个 备份 标签"
- LLM:调 `save_file_label("备份", "/sata14/my/data/")`(自动建)
- LLM:调 `list_file_labels()` 验证

### 例 5:用户说"删除 docker 标签"
- LLM:**先**调 `list_file_labels()` 确认存在
- LLM:**弹 UI 让用户确认**:"即将删除 docker 标签..."
- 用户确认后,调 `delete_label("docker")`

---

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| `RuntimeError: NAS_USER / NAS_PASSWORD env not set` | .env 没加载或没填 | `cp .env.example .env` + 填密码 |
| `找不到 /sata14/my/data/` | 路径错(忘了 `/`) | 目录路径必须 `/` 结尾 |
| `code=N001411 无权限` | 路径不在 `/<pool>/my/<子目录>/` | 用户只能扫自己 /池名/my/ 下 |
| `code=N001212 参数有误` | 字段名错或 JSON 而非 form | 走 MCP tool,不用脚本 |
| 扫目录很慢 | max-depth 太深或目录太多 | 改小 `--max-depth` 或换更窄的 `--root` |
| 找不到带标签的文件 | max-depth 不够 | 加大 `--max-depth`(但会更慢) |
| 标签删除后没生效 | NAS 缓存 | 几秒后刷新 |
| 标签名拼错 | delete 是按名字匹配 | 先 `list_file_labels()` 看准确名字 |

---

## NAS 字段类型坑(必读)

`/v2/file/list` 返回的 item 字段:
- `is_dir` 是字符串 `"0"` 或 `"1"`,**不是 bool**
- `size` / `modify_time` 是**字符串**(不是 int),脚本里要 `int()`
- `labels` 是**逗号分隔字符串**(如 `"docker"` 或 `"docker,重要"`),要 split
- 文件列表字段叫 `data.list`(**不是** `data.items`)

`/v2/labels/alllabels` 返回的 list 元素:
- `id` / `created_at` / `updated_at` / `weight` 是 int
- `label_name` 是 str
- `top_flag` 是 int(0/1)

---

## 后续可以做(等需求)

- **多轮打标**:用户连续说"再给它们打 XX 标签",Agent 维护上下文
- **冲突检测**:打标前检查文件已有标签,提示"已有 Y 标签,要覆盖吗"
- **RAG 联动**:见下方「跟 smart-tagger 协作」

---

## 跟 smart-tagger 协作(案例三)

label-manager 是**标签管理的底层工具**(标签 CRUD + 按元数据找文件)。
`smart-tagger` skill 是上层组合:**按内容找**(RAG 语义检索) + **批量打标**。

**怎么选**:

| 用户需求 | 走哪个 skill |
|---|---|
| 给 .yml 文件打 docker 标签(按扩展名) | **label-manager**(本 skill)场景 2 |
| 给"一年级教材"打标签(按内容) | **smart-tagger**(走 RAG) |
| 给 /特定目录/ 下所有文件打标 | **label-manager**(本 skill) |
| 找所有 docker 标签的文件(反向查) | **label-manager**(本 skill) |
| 新建/删除标签 | **label-manager**(本 skill) |

**smart-tagger 找到文件后,最终也调本 skill 的 `save_file_label`** — 所以本 skill 是打标的唯一入口(MCP 弹 UI 批准)。

详见 `skills/smart-tagger/SKILL.md`。