# ZSpace NAS MCP

89 个 MCP tool + 6 个 skill,让 Claude/Cursor 直接操作极空间 NAS。

## 你只需要其中一部分

**这个仓库包含 3 个独立组件,按需取用。不需要 clone 全部:**

| 你是 | 你需要 | 不需要 |
|---|---|---|
| **MCP 用户**(想让 Claude Code 操作 NAS) | `mcp_server/` + `nas/` + `.env` | Skill / Dashboard / RAG |
| **Skill 用户**(想用自动化工作流) | 复制 `.claude/skills/<name>/` 到自己项目 | MCP 源码 / Dashboard / RAG |
| **RAG 用户**(想要语义搜索) | `nas-rag-server/` docker compose | Skill / Dashboard |
| **开发者**(想加新 tool/skill) | clone 整个仓库 | — |

## MCP 用户(3 步装上)

```bash
# 1. 安装 Python 包
git clone <repo> && cd zspace-mcp-poc
pip install -e .                    # 或用 ./start.sh deps

# 2. 配置连接
cp .env.example .env && vi .env     # 填 NAS_HOST/USER/PASSWORD

# 3. 接入 Claude Code
./start.sh mcp-cfg                  # 打印配置 → 粘到 mcp.json
# 重启 Claude Code,89 tool 自动出现
```

首次验证: `python .claude/skills/nas-setup/scripts/check.py`

## Skill 用户(复制到你的项目)

```bash
# 把需要的 skill 复制到你的 Claude Code 项目
cp -r .claude/skills/nas-setup ~/your-project/.claude/skills/
cp -r .claude/skills/smart-tagger ~/your-project/.claude/skills/
# 前提: 你的项目也已配置 MCP(上一步)
```

skill 在 `.claude/skills/` 目录下,Claude Code 在该目录启动时自动发现。
当前 6 个 skill: `nas-setup`(前置) `smart-tagger` `media-organizer` `ios-memo-bak` `label-manager` `file-organizer`

## RAG 用户(Docker 部署到 NAS)

```bash
cd nas-rag-server
docker compose up -d                # image: coracoo/cherry:nas_rag
# 详细: nas-rag-server/README.md
```

## 所有可选组件

| 组件 | 安装方式 | 用途 |
|---|---|---|
| MCP(必须) | `pip install -e .` | 89 tool,Claude Code 连 NAS |
| Skill | 复制到 `.claude/skills/` | 6 个工作流,Agent 自动触发 |
| RAG docker | `docker compose up -d` | 语义搜索,部署在 NAS 上 |
| Dashboard | `./start.sh dashboard` | Web UI,iPhone 备忘录入口 |
| 百度网盘 | `scripts/netdisk_login.py` | OAuth 登录后再用 28 个 znetdisk tool |

```
用户对 Claude Code 说话                    ← 自然语言
        ↓
┌─ Skill 层(.claude/skills/) ──────────────┐
│ nas-setup / smart-tagger / media-organizer│  ← LLM 触发词 → 自动加载 SKILL.md
│ ios-memo-bak / label-manager / file-org  │  ← 组合多个 MCP tool 完成复杂流程
└──────────────────┬───────────────────────┘
                   ↓ MCP 协议(stdio,JSON-RPC 2.0)
┌─ MCP 层(mcp_server/) ────────────────────┐
│ 89 个 tool(按域分文件)                    │  ← Claude Code mcp.json 配置后自动发现
│ tools/{files,storage,zvideo,notebook,    │  ← 每个 tool = 1 个 NAS API 端点封装
│        znetdisk,proxy,rag,...}           │
└──────────────────┬───────────────────────┘
                   ↓ HTTP(nas/)
┌─ 协议层(nas/) ───────────────────────────┐
│ auth.py  RSA 登录 + device_id 选择        │  ← Python 库,Skill 和 MCP 都复用
│ client.py NasClient(token 自动续)       │
└──────────────────┬───────────────────────┘
                   ↓ HTTP
┌─ ZSpace NAS ─────────────────────────────┐
│ :5055 主 API(文件/影视/记事本/网盘...)     │
│ :8000 RAG docker(语义搜索,可选)           │
└──────────────────────────────────────────┘
```

**三者关系**: Skill 是"做什么"(工作流) → MCP 是"怎么做"(单步操作) → nas/ 是"怎么连"(协议)。新用户只需配 MCP,skill 自动生效。

## 必须 & 可选

| 组件 | 必须? | 说明 |
|---|---|---|
| `.env` 配置 | ✅ 必须 | NAS 连接信息(NAS_HOST/USER/PASSWORD) |
| `mcp_server.py` | ✅ 必须 | MCP stdio 服务,Claude Code 连它 |
| `nas-setup` skill | ✅ 推荐 | 首次跑,验证 env + 登录 + 可选组件 |
| `nas-rag-server/` docker | 可选 | RAG 语义搜索。不装也能用 86 个 tool,只是 semantic_search 不可用 |
| `app.py` Dashboard | 可选 | Web 管理界面(iPhone 备忘录入口等) |
| 百度网盘 OAuth | 可选 | 28 个 znetdisk tool 需要先登录 |

## 安装

```bash
git clone <repo>
cd zspace-mcp-poc

# 1. 配置连接(必须)
cp .env.example .env
vi .env   # 填 NAS_HOST / NAS_USER / NAS_PASSWORD

# 2. 装 Python 依赖(必须)
./start.sh deps

# 3. 接入 Claude Code(必须)
./start.sh mcp-cfg   # 打印配置片段,粘到 ~/.config/claude-code/mcp.json
# 重启 Claude Code → 89 个 tool 自动出现

# 4. 首次验证
python .claude/skills/nas-setup/scripts/check.py
# 输出 ✅✅✅ 即可

# 5. (可选) RAG 语义搜索
cd nas-rag-server && docker compose up -d    # 需要 NAS docker daemon

# 6. (可选) Web Dashboard
./start.sh dashboard   # http://localhost:15050
```

## 使用示例

```
用户在 Claude Code 里说: "给一年级教材打《一年级》标签"

Agent 内部执行流程:
  nas-setup skill 自动加载 → check.py 验证 .env/登录/RAG
  smart-tagger skill 自动加载(触发词"给 XX 打标签")
    → semantic_search("一年级 教材") → MCP tool → POST NAS RAG daemon
    → 返回 3 个匹配 {path, snippet, distance}
    → Agent 过滤 distance < 1.0 的
    → save_file_label("一年级", "path1,path2") → MCP tool → NAS API
    → MCP 客户端弹 UI 让用户批准
    → ✅ 完成
```

## 文件路由

```
zspace-mcp-poc/
├── nas/                  NAS 协议层
│   ├── auth.py           RSA 公钥 + device_id 自动选择
│   ├── proto.py          URL 公共参数
│   └── client.py         NasClient(token 自动续)
│
├── mcp_server/           MCP Server
│   ├── main.py           FastMCP 入口
│   └── tools/            按域分文件
│       ├── files.py      文件读写 + 标签
│       ├── storage.py    存储池/硬件/SMART/监控
│       ├── zvideo.py     极影视
│       ├── notebook.py   记事 (17)
│       ├── znetdisk.py   网盘
│       ├── proxy.py      远程访问
│       ├── shares.py     共享/下载
│       ├── media.py      音乐/相册
│       └── rag.py        RAG 语义搜索
│
├── app/                  Web Dashboard
│   ├── main.py           FastAPI + Session
│   └── routes/
│       ├── shortcut.py   iPhone 备忘录 → NAS 入口
│       ├── dashboard.py  WebUI
│       └── files.py,notebook.py,zvideo.py 文件/记事本/影视 CRUD
│
├── nas-rag-server/       RAG docker 服务(在 NAS 独立部署，作为文件索引)
│   ├── app/server.py     /search /reindex /index /unindex /status
│   ├── Dockerfile + docker-compose.yml
│   └── docs/03-API.md    REST 协议
│
├── .claude/skills/       5 个自动化 skill
│   ├── ios-memo-bak/     iPhone 备忘录 → 极空间记事本
│   ├── media-organizer/  极影视分类审计
│   ├── smart-tagger/     RAG 搜索基础上的批量文件标签管理
│   ├── label-manager/    标签管理
│   └── file-organizer/   文件库诊断
│
├── mcp_server.py / app.py  shim(入口兼容)
├── API.md                 NAS 全端点速查
├── MCP.md                 89 tool 详细文档
└── start.sh               一键启动(deps/mcp/dashboard/mcp-cfg)
```

## MCP Tool 清单(89)

### 文件 & 存储池 & 监控(20)

| Tool | 读/写 | 用途 |
|---|---|---|
| `list_files` | 读 | 列目录 |
| `file_info` | 读 | 单文件元数据 |
| `recent_files` | 读 | 最近访问 |
| `file_categories` | 读 | 按类型统计 |
| `list_storage_pools` | 读 | 存储池 & 磁盘 |
| `hardware_info` | 读 | 硬件槽位 |
| `smart_report` | 读 | SMART 磁盘健康 |
| `system_status` | 读 | NAS 综合状态 |
| `perf_snapshot` | 读 | SSH 实时性能 |
| `whoami` | 读 | 当前用户 |
| `mkdir` | 写 | 新建目录 |
| `rename` | 写 | 重命名 |
| `move` | 写 | 移动 |
| `copy` | 写 | 复制 |
| `remove` | ⚠️ 删除 | 不可逆,不进回收站 |

### 极影视(8)

| Tool | 读/写 | 用途 |
|---|---|---|
| `list_video_classes` | 读 | 分类列表(含 is_enable/is_system) |
| `latest_movies` / `suggested_movies` / `random_movies` | 读 | 影片浏览 |
| `list_video_dirs` | 读 | 源目录 |
| `get_video_classification_state` | 读 | 单个分类状态 |
| `add_video_classification` | 写 | 新建分类 |
| `link_folder_to_classification` | 写 | 关联源目录(带 is_enable=0 拒绝) |

### 记事本(17)

| Tool | 读/写 | 用途 |
|---|---|---|
| `notebook_list/info/search` | 读 | 浏览 & 搜索 |
| `notebook_allclassify/classifylist` | 读 | 分类树 |
| `notebook_totalsize/getconfig` | 读 | 统计 & 配置 |
| `notebook_historyinfo/historylist` | 读 | 历史版本 |
| `notebook_new/modify/delete` | 写 | CRUD |
| `notebook_pin/updatelabel/movenotepad` | 写 | 置顶/标签/移动 |
| `notebook_newclassify/deleteclassify/updateclassify` | 写 | 分类管理 |

### 百度网盘(28)— 需要 OAuth 登录

| 分组 | Tool | 用途 |
|---|---|---|
| auth | `znetdisk_auth_check/token/userinfo/logout` | OAuth oob 登录 |
| file | `znetdisk_file_list/download/upload/newdir` | 云盘文件管理 |
| task | `znetdisk_task_list/action` | 传输任务 |
| sync | `znetdisk_sync_add/list/open/close/delete/home` | NAS ↔ 云盘双向同步 |
| autobackup | `znetdisk_autobackup_*` (7) | 自动备份 |
| share | `znetdisk_share_verify/filelist/transfer/transfer_result` | ⭐ 分享链接转存 |
| fail | `znetdisk_fail_list` | 失败列表 |

### 共享 & 下载 & 远程访问(11)

| Tool | 用途 |
|---|---|
| `samba_status / webdav_status / ftp_status / dlna_status` | 共享服务状态 |
| `list_downloads / list_shares / list_nshares` | 下载 & 分享 |
| `proxy_login / proxy_url_for_port / proxy_fetch / proxy_list_whitelist` | zos 云代理 |

### 音乐 & 相册(3)

| Tool | 用途 |
|---|---|
| `list_songs` | 歌曲列表 |
| `list_albums` | 相册列表 |
| `list_album_feeds` | 相册内容 |

### RAG 语义搜索(3)— 需要 nas-rag-server docker

| Tool | 用途 |
|---|---|
| `semantic_search` | 自然语言搜文件内容 |
| `reindex` | 重建索引 |
| `index_status` | 索引概况 |

## Skill 清单(5)

| Skill | 触发词 | 用途 |
|---|---|---|
| `ios-memo-bak` | iPhone 备忘录同步 | 一键配置 iPhone Shortcut → NAS 记事本 |
| `media-organizer` | 极影视整理、frds 拆分 | 只读审计分类/源目录/影片抽样 |
| `smart-tagger` | 给 XX 内容打标签 | RAG 搜 → 批量 save_file_label |
| `label-manager` | 打标签、按标签找 | 标签 CRUD + 反向查询 |
| `file-organizer` | 重复文件、孤儿文件 | 文件库只读诊断 |

## 接入标准

### MCP Client(mcp.json)

```json
{
  "mcpServers": {
    "zspace-nas": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/mcp_server.py"],
      "env": {
        "NAS_HOST": "192.168.x.x",
        "NAS_USER": "<phone>",
        "NAS_PASSWORD": "<password>",
        "NAS_DEVICE_ID": "<32 hex>"
      }
    }
  }
}
```

### 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `NAS_HOST` | ✅ | NAS IP |
| `NAS_USER` | ✅ | 手机号 |
| `NAS_PASSWORD` | ✅ | 密码 |
| `NAS_DEVICE_ID` | 推荐 | 32 字符,复用已登记设备绕短信验证 |
| `KEY_SSH` | 可选 | perf_snapshot 需要 |
| `NAS_SSH_PORT` | 可选 | 默认 57922 |
| `NAS_RAG_URL` | 可选 | RAG daemon 地址,默认 `http://nas:8000` |

### 写操作安全规则

1. **destroy 类**(`remove`/`notebook_delete`) 不进回收站,MCP 客户端弹 UI 让用户批准
2. **状态校验**(`link_folder_to_classification`) 目标分类 is_enable=0 时直接拒绝
3. **标签覆盖**(`save_file_label`) 覆盖式,打新标签前先 `file_info` 看现有标签

## RAG docker 部署(可选)

```bash
cd nas-rag-server
docker compose up -d    # image: coracoo/cherry:nas_rag
# 首次跑 reindex
curl -X POST http://nas:8000/reindex -H 'Content-Type: application/json' \
  -d '{"scope":"files","full":true}'
```

REST API 详见 `nas-rag-server/docs/03-API.md`。

## 文档

| 文档 | 内容 |
|---|---|
| `API.md` | NAS 全端点速查(12 域,~900 行) |
| `MCP.md` | 89 tool 参数/返回/端点映射 |
| `nas-rag-server/docs/03-API.md` | RAG REST 协议 |
| `docs/iphone-shortcut.md` | iPhone Shortcut 配置图解 |

## License

MIT — 详见 [LICENSE](LICENSE)。欢迎 PR/Issue/Star。

[CONTRIBUTING.md](CONTRIBUTING.md)
