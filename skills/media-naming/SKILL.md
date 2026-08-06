---
name: media-naming
description: Use when 用户想按命名规范整理影视文件 — "帮我扫一下影视库命名有没有问题"、"电影文件夹名不规范"、"剧集文件名不合规"、"去掉水印/PT名"、"审查规避名怎么还原"、"合集要拆开"。正向合规扫描(scan)+LLM 按规范给出 rename/move 计划;写操作一律走 MCP tool 二次确认。
  触发词:影视命名、电影命名规范、剧集命名、文件名不合规、水印文件名、PT原始命名、审查规避、合集拆分、影视库扫描、重命名电影、整理影视文件名、media naming、rename movies。
  不适用:极影视分类审计(走 media-organizer)、标签管理(走 label-manager)、找重复/孤儿文件(走 file-organizer)。
---

# Media Naming — 影视文件命名正向校验

## 概述

**前置**:先调 `nas-setup` skill 验证 NAS 登录。

对物理目录做**正向合规扫描**:定义合规格式 → 不匹配即报问题。
**写操作不在脚本里** — 扫描出问题后,LLM 生成 `old → new` 映射,经用户确认后调 MCP tool。

> 通用命名方法论见 [media-naming-guide](https://github.com/skyzhao1223/media-naming-guide)。
> 本 skill 只覆盖极空间落地(分页 list、MCP 编排、踩坑)。

### 与 media-organizer 分工

| skill | 管什么 |
|-------|--------|
| **media-organizer** | 极影视 **分类/元数据** 审计(frds 混放、源目录) |
| **media-naming**(本 skill) | 文件系统 **命名规范**(文件夹/视频文件名) |

---

## 命令

| 命令 | 用途 |
|------|------|
| `scan --root PATH` | 正向合规扫描(只读) |
| `scan --root PATH --json` | stdout JSON |
| `scan --root PATH --output /tmp/issues.json` | 写 JSON 文件 |

```bash
python skills/media-naming/media_naming.py scan --root /sata14/my/data/影视
python skills/media-naming/media_naming.py scan --root /sata14/my/data/影视 --json --output /tmp/issues.json
```

期望目录结构:
```
影视/
  电影/
    中文名 English Name (年份) [分辨率]/
      中文名 English Name (年份) [分辨率].mkv
  剧集/
    中文名 English Name S01/
      中文名 English Name S01 E01.mp4
```

---

## 工作流

### 场景 1:用户说"帮我扫一下影视库命名"

**步骤**:
1. **exec** `python skills/media-naming/media_naming.py scan --root <用户路径> --output /tmp/issues.json`
2. 读输出 / JSON,按问题类型分组汇报(前若干条 + 总数)
3. **不做 rename** — 问用户要不要按规范出修复计划

### 场景 2:用户说"按规范修"

**步骤**:
1. 先跑 `scan`(或复用上一轮 `/tmp/issues.json`)
2. 按下方「整理顺序」生成 `old → new` 映射表,**先预览给用户**
3. 用户确认后,**逐条**调 MCP:
   - 垃圾文件 → `remove(paths=...)`
   - 错分类 → `move(paths=..., to=...)`
   - 改名 → `rename(path=..., newname=...)`(**newname 是纯文件名,不含路径**)
   - 缺目录 → `mkdir(parent=..., name=...)`
4. 再跑一遍 `scan`,问题数=0 才算完成

### 场景 3:用户说"这个审查规避名怎么还原"

**步骤**:
1. 不要凭正则脑补。按 media-naming-guide 的解码铁则:**搜索验证**后再定中文名
2. 确认后走 `rename` MCP tool

---

## 整理顺序(严格)

1. 删除垃圾文件(torrent / nfo / td / jpg / htm)
2. 移错分类(电影 ↔ 剧集)
3. 解码审查规避(人工映射 + 搜索验证)
4. 修复目录名(补英文名/年份、去水印/PT 后缀)
5. 拆分合集文件夹(`1-3` 结尾)
6. 对齐内部文件名(电影=文件夹名;剧集=`剧名 SXX EYY`)
7. 重新 `scan` 验证

每一步都先出映射表预览,确认后再批量执行。

---

## 命名速查

### 电影

```
中文名 English Name (年份) [分辨率 来源]/
  中文名 English Name (年份) [分辨率 来源].mkv
  花絮/
    花絮 - 视觉之旅.mkv
```

- 禁止合集文件夹:`钢铁侠 Iron Man 1-3` 必须拆成独立文件夹
- 分段电影用 `CD1`/`CD2` 留在电影目录(不要当剧集)

### 剧集

```
中文名 English Name S01/
  中文名 English Name S01 E01.mp4
  中文名 English Name S01 SP01 彩蛋.mp4
```

---

## MCP tool 依赖

| Tool | 用途 |
|------|------|
| `list_files` / `file_info` | 抽查、确认 |
| `rename(path, newname)` | 改名(newname=纯文件名) |
| `move(paths, to)` | 移错分类 / 拆合集 |
| `mkdir(parent, name)` | 建规范目录 |
| `remove(paths)` | 删垃圾(必须二次确认) |

---

## 关键约束

1. **脚本只读**:无 apply / rename 子命令;写操作走 MCP 弹 UI
2. **串行不并发**:每层 list sleep 0.1s;大批量 rename 注意总耗时
3. **rename 第二参数纯文件名**:含路径会失败
4. **路径**:用户只能扫 `/<pool>/my/<子目录>/`;目录建议 `/` 结尾传给 list 类 tool
5. **判断交回 LLM**:脚本只做机械校验;审查规避解码 / 英文名补全必须搜索验证
6. **先预览后执行**:永远先给 `old → new` 表

---

## 踩坑

| 坑 | 表现 | 解决 |
|----|------|------|
| 分页 | list 一次有上限 | 脚本用 `start`+`num` 循环 |
| `is_dir` 类型 | 字符串 `"0"`/`"1"` | 脚本已处理 |
| rename 参数 | 第二参数带路径失败 | 只用纯文件名 |
| 审查规避脑补 | 改错片 | 解码后必须搜索验证 |
| 分段电影误判 | CD1/CD2 当剧集 | 无 E01 且有 CD/Part → 留电影 |

---

## 故障排查

| 现象 | 处理 |
|------|------|
| `NAS_USER / NAS_PASSWORD env not set` | `cp .env.example .env` 并填写 |
| `code=N001411` | 路径越权,只扫自己的 `/池/my/` |
| 扫描很慢 | 缩小 `--root` 到 `电影` 或 `剧集` 子目录 |
