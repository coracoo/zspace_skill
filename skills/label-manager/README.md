# Label Manager Skill(开发者文档)

## 这是什么

Claude Code 的 skill 工作流,封装 ZSpace NAS 标签管理的**组合动作**。
MCP tool 是原子能力,这个 skill 是编排 + 机械活(扫目录、过滤匹配)。

## 目录结构

```
skills/label-manager/
├── SKILL.md            # LLM 读的工作流(触发词 + 5 场景 + 调用示例)
├── label_manager.py    # Python CLI:list-labels / scan / find-by-label
├── lib/
│   └── nas_client.py   # 桥接层:从 mcp_server.py import NasClient
├── tests/
│   └── smoke.sh        # 自动化烟雾测试
└── README.md           # 这个文件
```

## 复用 mcp_server.py 的方式

**直接 import mcp_server.py**(冷启动 0.77s):

```python
# lib/nas_client.py
PROJECT_ROOT = Path(__file__).resolve().parents[4]  # 项目根
sys.path.insert(0, str(PROJECT_ROOT))
_load_env()  # 必须在 import mcp_server 前加载 .env
from mcp_server import NasClient, _to_json
```

为什么直接 import:
- 简单,零代码改动
- 0.77s 冷启动可接受(用户场景是手动触发)
- 不重复实现 RSA 登录 + cookie 管理

如果以后觉得太慢,把 `NasClient` 抽到独立 `nas_client.py`,让 `mcp_server.py` 也 import 它。

## env 加载

`lib/nas_client.py` 里 `_load_env()` 在 `import mcp_server` **之前**调用。
原因:`mcp_server.py:88` 在 `NasClient.__init__` 里会读 `NAS_USER` / `NAS_PASSWORD` env,
如果 env 没设就 `RuntimeError`。

## 性能数据(N150 实测)

| 操作 | 范围 | 耗时 |
|------|------|------|
| `list-labels` | 4 个标签 | < 1s |
| `scan --ext yml --max-depth 2` | 192 个目录 | ~26s |
| `find-by-label --max-depth 3` | 755 目录 + 8413 文件 | ~110s |

串行不并发,每步 sleep 0.1s。max-depth 默认 5,但大目录建议先 2-3 试水。

## 测试

```bash
# 烟雾测试(列标签 + 扫目录 + 反向查)
bash skills/label-manager/tests/smoke.sh

# 单独测
.venv/bin/python skills/label-manager/label_manager.py list-labels
.venv/bin/python skills/label-manager/label_manager.py scan \
  --root /sata14/my/data/ --ext yml --max-depth 2
.venv/bin/python skills/label-manager/label_manager.py find-by-label \
  --label docker --root /sata14/my/data/ --max-depth 3

# SKILL.md 格式检查
python3 -c "
import yaml, re
content = open('skills/label-manager/SKILL.md').read()
m = re.match(r'^---\n(.+?)\n---', content, re.DOTALL)
print(yaml.safe_load(m.group(1)).keys())
"
```

## 已知 gap

- **扫目录无并发**:N150 会卡,但慢;如果以后想快,得先 NAS 端解决
- **find-by-label 受 max-depth 限制**:深度外文件找不到
- **没有 apply / delete 子命令**:写操作走 MCP tool(LLM 弹 UI 让用户确认)
- **最近文件 vs 全文件**:实测 `/v2/file/list` 返回的 item 都带 `labels` 字段,
  所以可以直接 BFS 拿,不需要走 `recent_files`(原来 plan 担心 992 项硬上限,实测不必要)

## 设计原则

1. **MCP 是原子,Skill 是编排** — 不重复造轮子
2. **复用 mcp_server.py 的 NasClient** — 不重复实现登录
3. **N150 限速** — 串行 + sleep 0.1s + max-depth 默认 5
4. **删除走 MCP tool,不走脚本** — 太危险,必须有 LLM 二次确认
5. **判断交回 LLM** — 脚本只做机械活(扫文件、过滤),决策(打哪些)LLM 做
6. **进度回调** — 每 20 个目录 print 一次,LLM 看到进度不会误以为卡死