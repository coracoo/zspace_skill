# ZSpace NAS PoC + MCP Server

两部分:
1. **Dashboard PoC**(`app.py`)— FastAPI Web UI,验证 NAS API 可用性,带总览/存储池/极影视/记事本/写测试 5 大 tab
2. **MCP Server**(`mcp_server.py`)— **58 个 tool**(40 读 + 18 写),让 Claude Code/Cursor 直接操作 NAS。
   RAG 语义搜索为**可选模块**(3 个 tool),需单独安装 `rag/` 包;未安装时自动禁用,详见下文「RAG」一节。

### 当前完成情况

| 模块 | 状态 | 说明 |
|------|------|------|
| Dashboard PoC(`app.py`) | ✅ 完成 | 5 个 tab,含 `/shortcut/notepad` iPhone 推备忘录入口 |
| MCP Server 58 tool | ✅ 完成 | 文件/存储池/监控/影视/音乐/相册/下载/分享/共享/记事本/远程访问 全覆盖 |
| 📒 记事本 MCP(17 tool) | ✅ 完成 | 8 读 + 9 写,含标签/分类/置顶/历史版本 |
| 🏷️ label-manager skill | ✅ 完成 | `.claude/skills/label-manager/` — 打标/批量打标/反向查找 |
| 🎬 media-organizer skill | ✅ 完成 | `.claude/skills/media-organizer/` — 只读诊断极影视分类 |
| 📱 iPhone Shortcut 同步 | ✅ 完成 | `docs/iphone-shortcut.md` — 备忘录 → NAS,emoji/entity 转换 + 渲染激活 |
| 🔍 RAG 全局语义搜索 | ⚠️ 可选 | bge-small-zh-v1.5 + sqlite-vec + fastembed,3 个 tool,写时被动增量。`rag/` 包未内置,需单独安装;未安装时自动禁用 |

## 一、Dashboard PoC

```bash
cd /home/cc/workspace/zspace-mcp-poc
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

浏览器打开 `http://192.168.0.123:8000`,用 NAS 账号登录。

**5 个 tab**:
- 📊 总览(`/zstatus` 解析:开机/负载/内存/磁盘/服务健康)
- 💾 存储池(`/zspool/info`:2 块 9.1TB WDC + Samsung SSD)
- 🎬 极影视(分类列表 + 影视库源目录 + 写测试 2 张卡片)
- 📒 记事本(location=2 独立记事本:读 + 9 张写测试 + 3 张下载测试)
- 🧪 写测试(storage 6 张卡片:mkdir/info/rename/move/copy/remove)

## 二、MCP Server(58 个 tool)

### 配置(Claude Code)

`~/.config/claude-code/mcp.json`(或 Cursor / Claude Desktop 对应位置):

```json
{
  "mcpServers": {
    "zspace-nas": {
      "command": "/home/cc/workspace/zspace-mcp-poc/.venv/bin/python",
      "args": ["/home/cc/workspace/zspace-mcp-poc/mcp_server.py"],
      "env": {
        "NAS_HOST": "192.168.0.135",
        "NAS_USER": "15068832031",
        "NAS_PASSWORD": "你的密码",
        "KEY_SSH": "你的密码",
        "NAS_DEVICE_ID": "a6b4bd9ea4839ab4aea6f22b558bf0b2"
      }
    }
  }
}
```

`NAS_DEVICE_ID` 默认借用已登记的设备(避免新设备短信验证),可选。
`ZENITH_COOKIE` 启用 4 个 `proxy_*` 工具的云代理转发功能(详见"远程访问代理")。

### 环境变量

| 变量 | 必填 | 用途 |
|------|------|------|
| `NAS_HOST` | ✅ | NAS IP(默认 192.168.0.135) |
| `NAS_USER` | ✅ | 用户名(手机号) |
| `NAS_PASSWORD` | ✅ | 密码 |
| `NAS_DEVICE_ID` | 可选 | 32 字符,默认借用 Firefox/151 的 |
| `KEY_SSH` | 可选 | perf_snapshot tool 需要 |
| `NAS_SSH_PORT` | 可选 | SSH 端口,默认 57922 |
| `ZENITH_COOKIE` | 可选 | 启用 `proxy_*` 工具,见下方"远程访问代理" |

### Tool 分类(58 个核心 + 3 个可选 RAG)

**读(40)**:

| 类别 | 数量 | 工具 |
|------|------|------|
| 📁 文件 | 5 | `list_files` `file_info` `recent_files` `file_categories` `list_file_labels` |
| 💾 存储池 | 4 | `list_storage_pools` `hardware_info` `pool_capability` `smart_report` |
| 🖥️ 监控 | 2 | `system_status` `perf_snapshot` |
| 🎬 影视 | 6 | `list_video_classes` `get_video_classification_state` `latest_movies` `suggested_movies` `random_movies` `list_video_dirs` |
| 🎵 音乐 / 📷 相册 | 3 | `list_songs` `list_albums` `list_album_feeds` |
| ⬇️ 下载 / 🔗 分享 | 3 | `list_downloads` `list_shares` `list_nshares` |
| 🌐 共享服务 | 4 | `samba_status` `webdav_status` `ftp_status` `dlna_status` |
| 🔍 其他 | 1 | `whoami` |
| 📒 记事本 | 8 | `notebook_list` `notebook_info` `notebook_search` `notebook_allclassify` `notebook_classifylist` `notebook_totalsize` `notebook_getconfig` `notebook_historyinfo` |
| 🌐 远程访问 | 4 | `proxy_login` `proxy_url_for_port` `proxy_fetch` `proxy_list_whitelist` |

**RAG 语义搜索(3,可选 — 需安装 `rag/` 包,否则不注册):**

| 类别 | 数量 | 工具 |
|------|------|------|
| 🔍 语义搜索 | 3 | `semantic_search` `reindex` `index_status` |

**写(18,⚠️ 真落盘)**:

| 类别 | 数量 | 工具 |
|------|------|------|
| 📁 文件 | 7 | `mkdir` `rename` `move` `copy` `remove` `save_file_label` `delete_label` |
| 🎬 影视 | 2 | `add_video_classification` `link_folder_to_classification` |
| 📒 记事本 | 9 | `notebook_new` `notebook_modify` `notebook_delete` `notebook_pin` `notebook_updatelabel` `notebook_movenotepad` `notebook_newclassify` `notebook_deleteclassify` `notebook_updateclassify` |

> ⚠️ **写工具会真落盘**。MCP 客户端(Claude Code/Cursor)调用时会弹 UI 让用户批准。
> `remove` **不进回收站,不可逆**。
> `notebook_delete` 第 1 次移到 trash,第 2 次永久删除,不可恢复。
>
> 🛡️ **状态校验**(2026-07-01 加):`list_video_classes` 现在带 `summary`(enabled/disabled 计数 + 禁用 ID/名字),`link_folder_to_classification` 在目标被禁用(`is_enable=0`)时**直接拒绝**,避免错把目录关联到关闭的分类。LLM 调错分类不会真落 NAS。

> 📋 详细每个 tool 的参数/返回/坑见 **`MCP.md`**。

### 🔍 RAG 全局语义搜索(`semantic_search` / `reindex` / `index_status` 3 个 tool)

把 NAS 已有内容(记事本 body + 文件名 + 文本文件内容)用 **bge-small-zh-v1.5**(中文 SOTA 小模型,~100MB,CPU 推理 ~50ms/条)embed 进向量,sqlite-vec 存,自然语言查询。

**为啥要 RAG**:NAS 自带 `notebook_search` 只接关键词,`list_files` 也不带全文搜。"去年写的 docker 笔记" 这种问法关键词搜不到,语义搜能命中。

**3 个 tool**:

| Tool | 作用 |
|------|------|
| `semantic_search(query, scope="all/files/notebooks", top_k=10)` | 跨 NAS 全局语义搜,返回 `[{source_type, source_path, snippet, score}, ...]` |
| `reindex(scope="all/notebooks/files", full=false)` | 重建索引(**后台跑**),返回 task_id,看 `index_status()` 跟踪 |
| `index_status()` | 索引概况(模型/条数/后台任务/最后 reindex 时间) |

**索引策略**:

- **范围**:记事本 body + 文件名 + **白名单文本文件内容**(<100KB,`.py`/`.md`/`.json`/`.yaml`/`.conf` 等),**不做影视/相册/音乐**
- **写时增量**:`notebook_new/modify/delete` + `mkdir/rename/move/copy/remove` 全部触发后台 RAG 同步(单条 < 1s,不阻塞主流程)
- **冷启动**:`reindex(scope="all", full=true)` 全量建索引(N150 上 100 条记事本 5-10 秒,1000 个文件 1-2 分钟)
- **存储**:`~/.cache/zspace-rag/rag.db`(~50MB / 1000 chunks),git ignore
- **依赖**:`fastembed`(ONNX,无 torch)+ `sqlite-vec`(pip 装,纯 Python + C 扩展)

**典型用法**:

```text
# 全量建索引(首次或文件内容被 NAS 端改了再跑)
> 帮我重建 NAS 索引
→ reindex(scope="all", full=true)
→ index_status()  # 等 done

# 自然语言搜
> 帮我找关于 docker 容器编排的笔记
→ semantic_search(query="docker 容器编排", top_k=5)

# 只搜文件
> 找所有 nginx 配置文件
→ semantic_search(query="nginx", scope="files", top_k=10)
```

**已知 gap**:

- 文件**内容**被 NAS 端直接改(没走 MCP)→ 索引跟不上,需要再跑 `reindex(scope="files", full=false)` 让 hash 没命中的重做
- `notebook_movenotepad`(只改分类,不改内容)不触发 RAG 同步
- 第一次跑要下载 bge 模型 ~100MB,之后缓存
- `proxy_*` 4 个 tool;核心 58 个(40 读 + 18 写)+ 可选 RAG 3 个 = 装了 RAG 时总计 61 个 tool。
  **`rag/` 包未内置在仓库里**,需要单独提供;`mcp_server.py` 启动时 `try: import rag.mcp_tools` 失败会打 warning 并跳过这 3 个 tool,不影响其余 58 个。

### 远程访问代理(`proxy_*` 4 个 tool)

zos 给每个 NAS 内网端口分配一个公网子域名:`https://remote-access-{port}.zconnect.cn/`
→ 自动代理到 NAS `127.0.0.1:{port}`(前提是白名单里有这个端口)。

这 4 个 tool 让 MCP 客户端能通过云代理访问 LAN 服务(类似 ngrok 但走 zos 官方):

| Tool | 作用 |
|------|------|
| `proxy_url_for_port(port)` | 生成公网 URL 模板(纯计算) |
| `proxy_fetch(port, path, method, body)` | 实际通过云代理发请求 |
| `proxy_list_whitelist` | 读白名单(gap:`/zrps/api/remoteaccess/list` 在 NAS 上是 dead route,只能从 pcweb UI 看) |
| `proxy_login` | 强制重新登录刷新 zenith session |

#### 启用条件

需要设置 `ZENITH_COOKIE` env,值是从浏览器 DevTools 复制的**完整** cookie 字符串:

1. 浏览器登录 pcweb(任意 *.zconnect.cn 子域都行)
2. F12 → Application → Cookies → 选 `https://remote-access-33335.zconnect.cn/`(或任意 .zconnect.cn 域)
3. 全选所有 cookie,Ctrl+C 复制
4. 整串(分号分隔的 `key=value`)塞进 mcp.json 的 `ZENITH_COOKIE`

**不设置 `ZENITH_COOKIE` 也能跑**,只是 `proxy_*` 会返回 302(被云代理踢去 SSO)。

#### 跟浏览器扩展配合

`browser-extension/` 是个 Chrome 扩展,把同一份白名单变成浏览器侧一键跳转(omnibox `zra <port>` + popup 列表)。三个工具一起用:

| 用途 | 工具 |
|------|------|
| MCP / Claude Code 程序化访问 | `mcp_server.py` 的 `proxy_fetch` |
| 浏览器侧一键跳转 | `browser-extension/` 扩展 |
| URL 模板生成 | `proxy_url_for_port` |

#### 已知 gap

- `/zrps/api/remoteaccess/list` 在 NAS 上返回 200 + 空 body(openresty 有路由没后端),白名单**只能从 pcweb UI 同步**
- FTP 转发:协议不匹配,zenith 云代理只懂 HTTPS
- `proxy_fetch` 走 cloud 是 **跨机代理**,`LAN_IP:port`(非 127.0.0.1)的条目大概率 502

### 验证

```bash
NAS_USER=15068832031 NAS_PASSWORD=... KEY_SSH=... .venv/bin/python mcp_server.py
# 看到日志: "MCP server 'zspace-nas' starting, 50 tools registered" 即成功
```

进阶冒烟测试(完整 MCP 握手 + 调用):

```bash
NAS_USER=15068832031 NAS_PASSWORD=... KEY_SSH=... .venv/bin/python -c "
import asyncio, json, os, sys
async def m():
    p = await asyncio.create_subprocess_exec(sys.executable, 'mcp_server.py', stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=4*1024*1024)
    async def send(m): p.stdin.write((json.dumps(m)+'\n').encode()); await p.stdin.drain()
    async def recv(): return json.loads(await p.stdout.readline())
    await send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'t','version':'1'}}})
    print(await recv())
asyncio.run(m())
"
```

## 三、故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 登录失败 `N001200 账号格式不对` | RSA 用错公钥(`server_pubkey` 而非 `pubkey`) | 用 `/zspace/system/private/pubkey` 解码后的 2048-bit PEM |
| `N001414 新设备需要短信验证` | device_id 不在 NAS 已登记列表 | 复用已登记的 device_id(默认值就是) |
| `/v2/file/list` `N001411 无权限` | path 越权 | 用户只能看 `/<pool>/my/<子目录>/`,不能直接 `/sata14/` |
| `/v2/file/list` `N001212 参数有误` | 字段名错或 JSON 而非 form | 必须 form-urlencoded + 带 `folderId/path/start/num/sortby/order/show_hidden` |
| `/zvideo/classification/increase` `N120020` | 字段名错 | 是 `file_path[]`(PHP 数组语法)不是 `file_path` |
| MCP 客户端"tool not found" | 服务没启动 | 看 stderr 日志确认登录成功 |
| MCP 大响应卡住 | readline 缓冲区 | 客户端 reader limit 调到 4MB+(`asyncio.subprocess` 默认 64KB 太小) |
| `proxy_fetch` 302 跳 www.zconnect.cn | `ZENITH_COOKIE` 缺失或过期 | 从浏览器 DevTools 重新复制 cookie |
| `proxy_fetch` 403 | 端口不在白名单 | pcweb UI 加端口到远程访问白名单 |
| `proxy_fetch` 502 | 目标不可达(跨机 LAN 条目) | 改成 `127.0.0.1:{port}` 而不是 LAN IP |

## 四、Skill 工作流

Claude Code 的 skill 是 MCP 之上的"组合动作"层,做 LLM 不擅长或容易漏的机械活。

### 现有 skill

| Skill | 触发词 | 用途 | 命令 |
|-------|--------|------|------|
| `label-manager` | 打标签、按标签找、新建标签、删除标签 | NAS 标签管理(打标/批量打标/反向查找) | `list-labels` / `scan` / `find-by-label` |
| `media-organizer` | 极影视整理、分类诊断、frds 拆分 | **只读**诊断极影视的分类不规范 | `audit-classifications` / `audit-sources` / `audit-collections` / `audit-all` |

### 工作模式

- 写在 `.claude/skills/<name>/SKILL.md` 里,LLM 看到触发词自动加载
- Python 脚本(`.claude/skills/<name>/*.py`)做"机械活"(扫文件、跑统计),LLM 决定"做什么"
- **写操作不走脚本**,走 MCP tool 让 LLM 弹 UI 让用户批(危险动作)

详细见各 skill 目录的 `README.md`。

## 五、相关文档

- **`API.md`**(907 行)— NAS 全端点速查 + 字段对照 + 易踩坑(目前覆盖到 §6.3.2)
- **`MCP.md`** — MCP 58 个 tool 详细文档(参数/返回/NAS 端点/坑)+ 覆盖差距
- **`docs/iphone-shortcut.md`** — iPhone 备忘录 → NAS 记事本 同步(4 动作 Shortcut 推 Cocoa HTML,iOS 端 0 密钥;服务端自动剥样式转 `<h1>/<h2>/<h3>/<p>/<table border=1>`,emoji 走 UTF-8 直传;备用 PWA 页面在 `/n`)
- **`templates/tab_*.html`** — Dashboard 5 个 tab 模板(总览/存储池/极影视/记事本/写测试)
- **`browser-extension/`** — Chrome 扩展,把白名单变成一键跳转(popup + omnibox `zra`)
- **`.claude/skills/label-manager/`** — 标签管理 skill
- **`.claude/skills/media-organizer/`** — 极影视诊断 skill
- **`/home/corain/.claude/plans/fizzy-enchanting-rossum.md`** — label-manager + media-organizer skill 实现计划
