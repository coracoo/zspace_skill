---
name: media-organizer
description: Use when 用户想诊断极影视分类问题 — "我的影视库分类有没有问题"、"frds 这个分类是不是太杂了"、"哪些影片分错类了"、"帮我出个影视库整理报告"。6 个命令:5 个只读审计(分类/源目录/影片抽样/挪动建议/一键全跑)+ 1 个跨库文件物理迁移(migrate,默认 dry-run)。
  触发词:极影视整理、影片库分类诊断、影视分类不规范、frds 拆分、整理影视库、影视分类合并建议、影片是否在对的分类、collection 错放、哪些影片分错类了、影视库审计、极影视诊断、影视文件挪库、文件放错库、跨库迁移、A 仓库的挪到 B 仓库、move file to right library、影视库文件挪动。
  不适用:写操作(合并分类/移动影片/重命名)— 这些走 MCP tool + LLM 二次确认,不在这 skill 范围。**例外 1**:挪文件物理位置让极影视自动重扫是可行路径(不走 MCP 写 tool,直接 move/rename MCP tool + 等 NAS 自己重扫)— 走 `migrate` 子命令(场景 5,基于 `migration-rules.yaml` 配置,默认 dry-run,逐条确认)。**例外 2**:整分类的 metadata 修改(collection 挪分类)无 NAS API 端点,只能 pcweb UI 改。
---

# Media Organizer — NAS 极影视只读诊断

## 概述

**前置**:先调 `nas-setup` skill 验证 NAS 登录(whoami)。如 N001208 自动重登,正常。

通过组合 NAS MCP tool + `media_organizer.py` 脚本,**只读**诊断极影视的"分类不规范"问题。
**所有写操作都不在 skill 里** — 报告生成后,用户手动决定怎么处理(用 MCP tool 弹 UI 批准)。

### 为什么只读
- 合并/移动会触发 NAS 重扫(`/classification/rescan`),可能跑 30+ 分钟
- 影片可能在 `/zspace/extdev/...`(外置设备,只读),移动不了
- 诊断先看问题,修复单独做

---

## 5 个诊断命令

| 命令 | 用途 | 走哪个 MCP / 端点 |
|------|------|-------------------|
| `audit-classifications` | 分类审计:重名 / 空 / 异常名 | `list_video_classes` → `/zvideo/classification/list` |
| `audit-sources` | 源目录审计:不该在影视库的路径 | `list_video_dirs` → `/zvideo/classification/dirs` |
| `audit-collections` | 抽样审计:type 分布 + 分类与 type 不匹配 | `random_movies` × N → `/zvideo/video/randomlist` |
| **`suggest-moves`** | **per-collection 挪分类建议:哪些影片应该到哪个分类** | 复用 audit-classifications + randomlist |
| `audit-all` | 一键全跑 | 上面四个一起 |

---

## 工作流

### 场景 1:用户说"看下我的影视库分类有没有问题"

**步骤**:
1. **exec** `python skills/media-organizer/media_organizer.py audit-classifications`
2. 读输出,识别 4 类问题:
   - 重名分类(典型:用户自建"电影" + 系统分类"电影")
   - 空分类(series=0 + coll=0)
   - 异常名(全英文缩写 / 含路径分隔符 / 未启用)
   - 用户分类跟系统分类同名(应该合并到系统分类)
3. **exec** `audit-sources` 看源目录
4. **exec** `audit-collections --sample 8` 抽样看 type 分布

### 场景 2:用户说"某个分类里的影片是不是该挪走"(per-collection 诊断)

**步骤**:
1. **exec** `audit-classifications` → 看到异常分类(frds / test 之类)
2. **exec** `suggest-moves --sample 30` → 列出每个疑似错放的 collection
3. 报告给出:
   - 按当前分类聚合(frds 里 X 部该挪)
   - 按目标分类拆分(其中 Y 部 → 电影,Z 部 → 电视剧)
   - 全量估算(基于抽样率 × classification collection_count)
   - 详情(前 20 部的标题 / 年份 / 评分)
4. ⚠️ **不做挪动** — NAS 没暴露"挪 collection 分类"API(10 个候选路径都 403),修复走 pcweb UI 或改源目录 + classification/rescan

**为什么需要单独命令**: `audit-collections` 只在"分类"级别看分布混不混,`suggest-moves` 在"collection"级别给具体标题和挪向建议。两者互补。

### 场景 2:用户说"frds 这个分类是不是太杂了"

**步骤**:
1. **exec** `audit-classifications` → 确认 frds 是异常名
2. **exec** `audit-collections --sample 12`(更多采样)→ 看 frds 里都有什么 type
3. 报告里说"frds = 53 电影 + 37 电视剧",建议拆分到 `电影`/`电视剧`(系统分类)

### 场景 3:用户说"我有个 music 目录误加入影视库了,怎么去掉"

**步骤**:
1. **exec** `audit-sources` → 找到 `/sata14/my/data/music` 在可疑列表里
2. 报告说"需要在某个分类的关联里移除这个目录"
3. **不在 skill 里执行** — 让 LLM 调 MCP tool `add_video_classification`/`link_folder_to_classification` 之外需要先破的 `classification/rmdir` 端点(目前 MCP 没暴露),或者直接 pcweb UI 手动

### 场景 4:用户说"出综合诊断报告"

**步骤**:
1. **exec** `audit-all --sample 8 --output /tmp/audit.json`
2. 读 stdout(给人看) + `/tmp/audit.json`(详细 JSON)
3. 头部摘要显示发现 N 类问题,后面分 section 详细列

### 场景 5:用户说"A 仓库下有 XX 影片,应该挪到 B 仓库"

(基于 `migration-rules.yaml` 配置,启发式匹配 filename pattern → target library)

**步骤**:
1. **首次跑**:复制 `skills/media-organizer/migration-rules.yaml.example` 为 `migration-rules.yaml`,编辑 `libraries[].expected_host_paths` 和 `move_rules`(声明每个库的预期 path + filename pattern)
2. **exec** `python skills/media-organizer/media_organizer.py migrate --dry-run` → 打印计划(只读,不动 NAS)
3. **审计划**:每条候选 src/dst/reason,让 LLM 走确认
4. **exec** `python skills/media-organizer/media_organizer.py migrate --apply` → 实际 move(逐条确认)
5. **exec** `python skills/media-organizer/media_organizer.py migrate --apply --yes` → 全自动(LLM 已全审过 dry-run)
6. 完成后 LLM 提示「需要 trigger classification/rescan 让 NAS 重新刮削吗?」(走 MCP `link_folder_to_classification` 不需要,直接 rescan)

**关键约束**:
- 默认 dry-run,移动是物理破坏性操作
- 目标路径前缀 `/zspace/extdev/` 一律跳过(只读外置设备)
- `move_rules.target` 必须在 `libraries.name` 里(配置时校验)
- 改源目录后 NAS 不会自动重扫,需要 LLM 调 classification/rescan
- NAS API 不暴露 library↔path 的 binding(`/zvideo/classification/dirs` 不带 `classification_name`,`/zvideo/classification/list` 不带 `file_path[]`),所以**配置是规则唯一可靠来源**

**为什么需要单独命令**: 场景 1-4 是「找出问题」(audit),场景 5 是「物理修复」(迁移)。两者抽象层级不同 — audit 操作 classification metadata,migrate 操作文件系统。合并到一个命令会让 audit 变慢且带副作用。

---

## 关键约束(必读)

1. **只读诊断,不动 NAS** — 这个 skill 写的是"找出问题",不是"修复问题"
2. **修复走 MCP tool** — 合并/移除/重命名 用现有的 MCP `add_video_classification` / `link_folder_to_classification`,LLM 弹 UI 让用户批
3. **NAS 没"按分类列 collection"全量端点** — `series/list` count=0 已知 bug,只能 randomlist 抽样
4. **采样覆盖有限** — `randomlist` 每次 12 部,理论最多覆盖 ~150 部(8 次 + 去重),对于 1459 总数是 ~10% 抽样
5. **type 字段语义** — type=100 电影、200 电视剧、300 综艺/其他(从抽样推断,可能有未列出的值)
6. **路径权限** — `/zspace/extdev/...` 外置设备只读,移动需要 NAS 后台操作
7. **系统分类重命名/合并** — 系统分类(`is_system=1`)只能由 NAS 端处理,用户无法修改

---

## NAS 字段类型坑(实测)

`/zvideo/classification/list` 返回的元素:
- `is_system` / `is_enable` / `auto_series` / `not_scrape` / `share_users` 等都是 int(0/1)
- `name` 是 str
- `id` 是 UUID string(注意,不是 int)
- `collection_count` 是 int
- `series_count` 是 int(0 表示没自动分组)

`/zvideo/video/randomlist` 返回的元素:
- `type` 是 int(100/200/300...)
- `classification_id` / `collection_id` 是 string
- `release_year` / `score` 是 number(0 表示没元数据)
- `extend_type` 是 int(NAS 内部扩展类型,文档不全)

`/zvideo/classification/dirs` 返回的 data 是**纯字符串数组**(不是 list of dict)。

---

## 已知 gap

- ❌ **没有"按分类列 collection"全量端点** — 抽样可能漏掉某些 type
- ❌ **没有"挪 collection 分类"端点** — 10 个候选路径(`/zvideo/collection/move`、`chclass`、`changeclass` 等)都 403 forbidden,用户级没法直接改 collection 的 classification_id
- ❌ **randomlist 当前返回极不稳定** — 实测 50 次只回 0-5 部 unique(以前是 12 部 × N),NAS 行为波动
- ❌ **rename 端点未破** — 想重命名 frds → 老友记 需要先破 NAS 字段
- ❌ **type 字段完整语义** — 没文档,只能从抽样推断
- ❌ **跳过合并 / 删除分类** — `classification/del` / `classification/editname` MCP 没暴露,要做先破
- ❌ **分类下文件路径看不到** — randomlist 返回的 `file_path=""`(没填),所以无法从 collection 反查物理文件
- ❌ **classification/dirs 不带 binding** — 返回路径但 `classification_name` 是空(`—`),`classification/list` 也不带 `file_path[]` 数组。Binding 是 write-only(`/zvideo/classification/increase`)。从 dirs 反查哪个路径属于哪个分类需要用户配置(`migration-rules.yaml`)

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 抽样全部在 frds | randomlist 加权偏向最近 / 大分类 | 加大 `--sample`(16+),或承认 frds 就是"什么都往里塞" |
| `suggest-moves` 显示"0 部采样"或集中在小分类(音乐/夸克) | NAS randomlist 当前返回量低或偏向 | 隔几分钟重跑 / 加大 `--sample` / 接受覆盖不全 |
| `suggest-moves` 只看异常分类下的 collection(尊重用户自建) | 设计保守 — 用户自建的"华语电影"等不被当作异常 | 这是 by design,避免误报;要全量判断得改逻辑 |
| type=999 等奇怪值 | 没在 TYPE_LABELS 里映射 | 用 `--output` 看 JSON 原值 |
| 可疑路径误报 | 用户确实把 music 目录里部分视频文件分类了 | 看上下文,人工判断 |
| audit-all 报 0 部去重 | randomlist 返回空(网络问题) | 重跑 |

---

## 后续可以做(等用户要求再加)

- **合并建议脚本**:把用户分类的 collection 列表出来,跟系统分类对比,建议合并
- **重复检测**:跨分类的同名 / 同 collection_id 检测
- **孤立文件检测**:扫描源目录里的影片文件,看哪些没被任何 collection 收录
- **磁盘空间分析**:按分类统计磁盘占用,找大头