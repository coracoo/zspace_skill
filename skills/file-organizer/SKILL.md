---
name: file-organizer
description: Use when 用户想诊断 ZSpace NAS 文件库,找出重复文件 + 孤儿文件(只读,不动 NAS)。
  触发词:重复文件、孤儿文件、文件整理诊断、哪些文件重复了、哪些文件没归类、找重复电影、找重复照片。
  不适用:写操作(删除重复 / 移动孤儿)— 这些走 MCP tool(remove/move/save_file_label)+ LLM 二次确认,不在这 skill 范围。
---

# File Organizer — NAS 文件只读诊断

## 概述

通过组合 NAS MCP tool + `file_organizer.py` 脚本,**只读**诊断文件库的"重复"和"孤儿"问题。
**所有写操作都不在 skill 里** — 报告生成后,用户手动决定怎么处理。

### 为什么只读

- 删除/移动会丢失数据,需用户确认
- 没法精确反查"哪些文件已打标签"(NAS 端点不暴露 label↔file 关联表)
- 诊断先看问题,修复单独做

---

## 3 个诊断命令

| 命令 | 用途 | 走哪个端点 |
|------|------|-----------|
| `audit-duplicates` | 扫全盘找重复文件(按 size+ext 弱指纹) | `/v2/file/list` + `/zspool/info` |
| `audit-orphans` | 找无标签 + 非影视目录的孤儿文件 | `/v2/file/list` + `/zvideo/classification/dirs` |
| `audit-all` | 一键全跑(重复 + 孤儿) | 上面两个一起 |

---

## 工作流

### 场景 1:用户说"我 NAS 上有没有重复文件"

**步骤**:
1. **exec** `python skills/file-organizer/file_organizer.py audit-duplicates --output /tmp/dups.json`
2. 读 stdout 摘要 + JSON 详细清单
3. 报告:总扫描 N 个文件,发现 X 组重复,共 Y 个冗余文件,占用 Z GB
4. 列出前 20 组最大的重复(用户优先处理收益大的)
5. ⚠️ **重要**: 由于使用弱指纹(size+ext),误报率较高,需人工核对后再决定删除

### 场景 2:用户说"哪些文件没归类"

**步骤**:
1. **exec** `python skills/file-organizer/file_organizer.py audit-orphans --output /tmp/orphans.json`
2. 报告:总扫描 N 个文件,X 个孤儿(无标签 + 非影视)
3. 建议处理:
   - 大文件孤儿 → 考虑删除或归档
   - 文档/图片孤儿 → 建议打标签(save_file_label)
   - 临时/缓存文件 → 候选清理

### 场景 3:用户说"出综合诊断报告"

**步骤**:
1. **exec** `python skills/file-organizer/file_organizer.py audit-all --output /tmp/full-report.json`
2. 读 stdout(给人看) + JSON(详细数据)
3. 两个 section:重复文件诊断 + 孤儿文件诊断

---

## 关键约束(必读)

1. **只读诊断,不动 NAS** — 这个 skill 是"找出问题",不是"修复"
2. **修复走 MCP tool** — 删/移/打标 用现有 MCP `remove` / `move` / `save_file_label`,LLM 弹 UI 让用户批
3. **`remove` 不进回收站,不可逆** — 处理重复文件时优先 `move` 到 `_to_review/`,人工确认后再删
4. **指纹策略**(经 Task 7 验证):
   - NAS `/v2/file/list` 返回的 `file_hash` 对真实文件也返回空字符串
   - 无法精确去重 → 用 `(size, ext)` 弱指纹分组
   - **误报率高**(同 size 同 ext 但内容不同),需人工核对
5. **扫描范围** — 默认所有池的 `/<pool>/my/data/` 下(注意是 `/my/data/`,不是 `/my/`)
6. **性能约束** — 单线程顺序扫描,每页 200 条,每次请求后 sleep 0.1s(~10 req/s)

---

## 命令行选项

### 通用选项(所有命令都支持)

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--pool <NAME>` | (所有池) | 限定扫描某个池 |
| `--output <PATH>` | (不写文件) | 将 JSON 详细结果写入文件 |
| `--sample <N>` | 0(不限) | 限制扫描文件数(测试用) |
| `--min-size <MB>` | 1 | 忽略小于 N MB 的文件 |

### 示例

```bash
# 扫所有池找重复,只看 >= 10MB 的文件
python skills/file-organizer/file_organizer.py audit-duplicates --min-size 10

# 只扫 sata14 池,限制 1000 个文件(测试)
python skills/file-organizer/file_organizer.py audit-orphans --pool sata14 --sample 1000

# 综合诊断,写 JSON
python skills/file-organizer/file_organizer.py audit-all --output /tmp/audit.json
```

---

## 输出格式

### stdout 文本摘要(给人看)

```
======================================================================
file_organizer — 重复文件诊断报告
======================================================================

指纹策略: size+ext(weak)
  NAS list_files 的 file_hash 对真实文件也返回空字符串(Step 7.1 验证),
  无法用于精确去重。本策略用 (size, ext) 弱指纹分组,组内 >1 即候选重复。
  误报率较高(同 size 同 ext 但内容不同),需人工核对。

扫描池: sata14
已扫描文件: 15234
因 < min_size 跳过: 8912
访问目录数: 342

重复组数: 187
浪费总空间: 45.23 GB (48564160032 bytes)

Top 20 候选重复组(按浪费空间降序):
  [1] size=1.23 GB count=3 wasted=2.46 GB
      fp=size:1318584546|ext:zip
      - /sata14/my/data/downloads/backup.zip
      - /sata14/my/data/backup.zip
      - /sata14/my/data/archives/backup.zip
  ...
```

### JSON 详细结果(程序处理)

```json
{
  "cmd": "audit-duplicates",
  "strategy": "size+ext(weak)",
  "strategy_note": "NAS list_files 的 file_hash 对真实文件也返回空字符串...",
  "pools_scanned": ["sata14"],
  "pools_skipped": [],
  "total_scanned": 15234,
  "total_skipped_small": 8912,
  "dirs_visited": 342,
  "duplicate_groups": 187,
  "total_wasted_bytes": 48564160032,
  "total_wasted_human": "45.23 GB",
  "duplicates": [
    {
      "fingerprint": "size:1318584546|ext:zip",
      "size": 1318584546,
      "count": 3,
      "wasted_bytes": 2637169092,
      "paths": ["/sata14/my/data/downloads/backup.zip", ...]
    }
  ],
  "errors": [],
  "truncated": false
}
```

---

## 已知 gap

- ❌ **没法精确反查"哪些文件已打标签"** — NAS 端点不暴露 label↔file 关联表,orphan 检测只能退化为"非影视目录 + 无标签 ID 集合"
- ❌ **大文件 hash 计算** — NAS 没有暴露 chunk hash 端点,只能靠 list_files 返回的元数据(且 file_hash 为空)
- ❌ **跨用户共享目录** — `/<pool>/share/` 不在扫描范围(默认只扫 `/<pool>/my/data/`)
- ❌ **误报率高** — 弱指纹策略导致同 size 同 ext 但内容不同的文件被误判为重复

---

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 扫描卡住 | NAS list_files 限流或网络慢 | 加 `--sample 1000` 限制条数,或等待 |
| `audit-duplicates` 误报多 | NAS 不返回 hash,用弱指纹 | 报告里说明,人工核对;勿批量自动删除 |
| `audit-orphans` 报错"找不到 label 端点" | NAS 端点未破或网络问题 | 查 JSON errors 字段详情 |
| 某个池显示"perm"跳过 | `/my/` 无权限,应扫 `/my/data/` | 脚本已自动修正,但若仍报错检查池权限 |
| N150 性能问题 | 扫描太密集 | 脚本已内置 sleep 0.1s(~10 req/s),若仍卡顿加 `--sample` |

---

## 后续可以做(等用户要求再加)

- **执行 agent**:升级为弹 UI 二次确认后真删/真移(skill 外,走 MCP tool)
- **照片按 EXIF 整理**:用 EXIF 时间线分组,建议命名规范
- **冷热分层**:基于 `recent_files` 找长期未访问的,建议从 SSD 移到 HDD
- **大文件审查**:专门找 >10GB 的文件,分类列出
- **精确去重**:若 NAS 后续暴露 file_hash,可升级为精确去重
