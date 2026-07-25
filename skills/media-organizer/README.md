# Media Organizer Skill(开发者文档)

## 这是什么

Claude Code skill,**只读**诊断 ZSpace NAS 极影视的"分类不规范"问题。
输出报告,所有写操作(合并/移动/重命名)走 MCP tool + LLM 二次确认 — 不在 skill 里。

## 目录结构

```
skills/media-organizer/
├── SKILL.md             # LLM 读的工作流(触发词 + 4 命令 + 场景示例)
├── media_organizer.py   # Python CLI:audit-classifications / sources / collections / all
├── lib/
│   └── nas_client.py    # 桥接层:从顶层 nas 包 import NasClient
├── tests/
│   └── smoke.sh         # 自动化烟雾测试
└── README.md            # 这个文件
```

跟 `label-manager` skill 一样复用顶层 `nas` 包的 `NasClient`(共享 RSA 登录 + cookie)。

## 5 个命令

| 命令 | 输出 | 耗时(实测) |
|------|------|------------|
| `audit-classifications` | 重名/空/异常名分类 | < 2s |
| `audit-sources` | 可疑源目录 | < 2s |
| `audit-collections --sample N` | 影片 type 分布 + 分类一致性 | ~ 2s × N 次 randomlist |
| `suggest-moves --sample N` | **per-collection 挪分类建议**(标题/年份/评分/挪向) | ~ 2s × N 次 randomlist |
| `audit-all` | 综合报告(头部摘要 + 5 section) | 上面总和 |

## 关键设计:为什么只读

合并 / 移动 / 重命名分类**会触发 NAS 重新扫描**(`/classification/rescan`),
可能跑 30+ 分钟。如果用户在 MCP 客户端点错,NAS 会卡半天。

所以 skill 只生成**报告**,**修复动作**走 MCP tool 让用户弹 UI 批准(本来就是这套路)。

## 已知 gap(影响诊断准确度)

| gap | 影响 | 缓解 |
|-----|------|------|
| NAS 没"按分类列 collection"全量端点(`series/list` count=0) | 只能抽样,大分类可能漏 | `audit-collections --sample 12` 多采 |
| type 字段完整语义没文档 | `type=999` 等值无法映射 | `--output` 看 JSON 原值 |
| 影片 `file_path=""` 空 | 无法从 collection 反查物理文件 | 等 NAS 暴露 |
| rename 端点字段未破 | 不能脚本里重命名 | 用户手动 pcweb UI |

## 实测发现(NAS 当前状态,2026-07-01)

跑 `audit-all --sample 6` 报告(节选):
```
⚠️ 发现 6 类问题:
  - 重名分类 2 组(电影、电视剧)
  - 用户分类跟系统同名 2 个
  - 空分类 2 个(系统分类)
  - 异常名 2 个(frds、test)
  - 可疑源目录 2 个(/sata14/my/data/备份/test, /sata14/my/data/music)
  - 分类与 type 不匹配 1 个(frds 里 36 电影 + 33 电视剧)

'frds' = 69 部(电影×36 + 电视剧×33)
```
用户 1459 部影片里 1229 部塞在 frds(疑似"老友记"分类)里,实际有电影 + 电视剧混着。

## 测试

```bash
# 单独跑
.venv/bin/python skills/media-organizer/media_organizer.py audit-classifications
.venv/bin/python skills/media-organizer/media_organizer.py audit-sources
.venv/bin/python skills/media-organizer/media_organizer.py audit-collections --sample 8
.venv/bin/python skills/media-organizer/media_organizer.py suggest-moves --sample 30 --output /tmp/moves.json
.venv/bin/python skills/media-organizer/media_organizer.py audit-all --sample 8 --suggest-sample 30 --output /tmp/audit.json

# 烟雾测试
bash skills/media-organizer/tests/smoke.sh

# SKILL.md 格式检查
python3 -c "
import yaml, re
content = open('skills/media-organizer/SKILL.md').read()
m = re.match(r'^---\n(.+?)\n---', content, re.DOTALL)
print(yaml.safe_load(m.group(1)).keys())
"
```

## 设计原则

1. **MCP 是原子,Skill 是编排** — 跟 label-manager 一致
2. **只读诊断,不动 NAS** — 修复走 MCP tool 让 LLM 弹 UI 批准
3. **抽样覆盖有限就承认** — 不假装全量审计,README 里明说
4. **报告友好于人** — `audit-all` 给人看的格式有头部摘要 + ⚠️ 标记 + 建议上下文
5. **JSON 给程序** — `--output` 写详细 JSON 供后续处理