# ZSpace NAS PoC + MCP Server

两部分:
1. **Dashboard PoC**(`app.py`)— FastAPI Web UI,验证 NAS API 可用性,带监控/存储池/文件夹/极影视/写测试 6 大区块
2. **MCP Server**(`mcp_server.py`)— 26 个只读 tool,让 Claude Code/Cursor 直接操作 NAS

## 一、Dashboard PoC

```bash
cd /home/cc/workspace/zspace-mcp-poc
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

浏览器打开 `http://192.168.0.123:8000`,用 NAS 账号登录。

**区块**:
- 🖥️ 监控(`/zstatus` 解析:开机/负载/内存/磁盘/服务健康)
- ⚡ 性能监控(SSH `/proc`:CPU/温度/网络/Top 进程,5 秒缓存)
- 💾 存储池(`/zspool/info`:2 块 9.1TB WDC + Samsung SSD)
- 📁 文件夹(只读,可下转,面包屑导航)
- 🎬 极影视(分类列表 + 影视库源目录)
- 🧪 写测试(7 个写 API 表单:newdir/分类/加目录/info/rename/move/copy/remove)

## 二、MCP Server(26 个只读 tool)

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

### 环境变量

| 变量 | 必填 | 用途 |
|------|------|------|
| `NAS_HOST` | ✅ | NAS IP(默认 192.168.0.135) |
| `NAS_USER` | ✅ | 用户名(手机号) |
| `NAS_PASSWORD` | ✅ | 密码 |
| `NAS_DEVICE_ID` | 可选 | 32 字符,默认借用 Firefox/151 的 |
| `KEY_SSH` | 可选 | perf_snapshot tool 需要 |
| `NAS_SSH_PORT` | 可选 | SSH 端口,默认 57922 |

### 26 个 Tool 分类

| 类别 | 工具 |
|------|------|
| **文件**(4) | `list_files` `file_info` `recent_files` `file_categories` |
| **存储池**(4) | `list_storage_pools` `hardware_info` `pool_capability` `smart_report` |
| **监控**(2) | `system_status` `perf_snapshot` |
| **影视**(5) | `list_video_classes` `latest_movies` `suggested_movies` `random_movies` `list_video_dirs` |
| **音乐/相册**(3) | `list_songs` `list_albums` `list_album_feeds` |
| **下载/分享**(3) | `list_downloads` `list_shares` `list_nshares` |
| **共享服务**(4) | `samba_status` `webdav_status` `ftp_status` `dlna_status` |
| **其他**(1) | `whoami` |

### 验证

```bash
NAS_USER=15068832031 NAS_PASSWORD=... KEY_SSH=... .venv/bin/python mcp_server.py
# 看到日志: "MCP server 'zspace-nas' starting, 26 tools registered" 即成功
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

## 四、相关文档

- **`API.md`**(863 行)— NAS 全端点速查 + 字段对照 + 易踩坑
- **`templates/dashboard.html`** — Dashboard 6 大区块模板
- **`/home/corain/.claude/plans/fizzy-enchanting-rossum.md`** — MCP 实现计划

## 五、Git 历史

```
v0.4-services-expanded — 启用更多服务后的 API 扩展
v0.3-api-complete     — API 摸清里程碑
```
