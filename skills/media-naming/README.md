# Media Naming Skill(开发者文档)

## 这是什么

Agent skill:**只读**扫描 ZSpace NAS 影视目录的命名合规性。
输出问题清单;rename / move / remove 走 MCP tool + 二次确认。

与 `media-organizer` 互补:
- organizer → 极影视分类元数据
- naming → 物理文件/文件夹命名

## 目录结构

```
skills/media-naming/
├── SKILL.md            # LLM 工作流(触发词 + 场景)
├── media_naming.py     # CLI:scan
├── lib/
│   └── nas_client.py   # 桥接顶层 nas.NasClient
├── tests/
│   └── smoke.sh        # 烟雾测试
└── README.md           # 本文件
```

## 复用 nas 包

与 label-manager 相同:

```python
# lib/nas_client.py
PROJECT_ROOT = Path(__file__).resolve().parents[3]
_load_env()  # 必须在 import nas 前
from nas import NasClient
```

## 命令

```bash
.venv/bin/python skills/media-naming/media_naming.py scan \
  --root /sata14/my/data/影视

.venv/bin/python skills/media-naming/media_naming.py scan \
  --root /sata14/my/data/影视 --json --output /tmp/issues.json
```

期望 `--root` 下有 `电影/` 与/或 `剧集/` 子目录;其他区域跳过校验。

## 设计原则

1. **正向验证** — 定义合规格式,不枚举脏模式
2. **MCP 是原子,Skill 是编排** — 脚本不做写操作
3. **复用顶层 NasClient** — 不重复 RSA 登录
4. **限速** — 串行 + sleep 0.1s + max_depth 8
5. **判断交回 LLM** — 审查规避 / 英文名必须搜索验证

## 测试

```bash
bash skills/media-naming/tests/smoke.sh
```

离线部分只检查 SKILL.md frontmatter 与模块结构。
连 NAS 的 scan 需有效 `.env`(可选,跳过时打印 SKIP)。

## 已知 gap

- 目录约定写死 `电影`/`剧集` 中文名;自定义库名需改 validate 逻辑或先软链/别名
- 审查规避无法全自动还原
- 合集拆分的「每部年份」需 LLM 搜索补全,脚本只标记问题

## 参考

- 通用规范:[media-naming-guide](https://github.com/skyzhao1223/media-naming-guide)
- Issue:#5
