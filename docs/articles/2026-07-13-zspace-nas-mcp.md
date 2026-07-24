# 让 Claude 直接操作你的极空间 NAS:一份完整的逆向 + MCP 实战笔记

> 把一台 ZSpace 极空间 NAS(Z4Pro,N150 CPU,2 块 9.1TB WDC + Samsung SSD)从"只能点官方 app"变成"AI agent 自动整理文件、同步备忘录、管影视库、转存百度网盘分享"。
>
> 全程纯逆向,不刷固件,不动 bootloader。86 个 MCP tool、4 个自动化 skill、3 个真实使用 case,完整公开。

---

## 0. 为什么折腾这个

极空间 NAS 硬件不错,软件封闭:官方 app 只能点按,自动化为零。下面这些"看起来简单"的需求,官方都没给路子:

- "找一下我去年写的关于 docker 的笔记" —— 关键词搜不到,语义搜不到
- iPhone 备忘录想自动同步到 NAS 记事本 —— 没有官方入口
- 别人发个百度网盘分享链接,想转存到我的 NAS 自动下载 —— 要点 5 下鼠标
- 极影视分类乱了,哪些影片分错类了 —— 没诊断工具

本质问题是:**NAS 是个黑盒,只能按官方 UI 设计的路径操作**。

解决思路分两层:

1. **逆向 web API** —— 把黑盒打开
2. **用 MCP 协议暴露给 LLM** —— 让 AI 直接操作,不用人点鼠标

下面是完整的踩坑记录。

---

## 1. 入口:先找到 NAS 的 web API

极空间 NAS 内部跑着一套 web 服务,官方 pcweb 就是基于它。SSH 进去 `ps -ef | grep nginx`,看到 openresty 进程:

```
nginx: master process /zspace/applications/services/openresty/nginx/openresty \
  -p /zspace/applications/services/openresty/nginx/ \
  -c /zspace/applications/services/openresty/nginx/conf/nginx.conf
```

主入口是 `http://<NAS_IP>:5055`,几乎所有功能都在这个端口。少部分功能走独立 socket(`zdrive.socket`、`znetdiskv2_server` 等)。

### 1.1 登录:RSA-PKCS1v15

NAS 登录是 RSA 加密的。公钥从公开端点 `/zspace/system/private/pubkey` 拿,解码后是 2048-bit PEM。**关键坑:有两个公钥端点**(`pubkey` 和 `server_pubkey`),用错会报 `N001200 账号格式不对` —— 正确的是 `pubkey`,不是 `server_pubkey`。

加密流程:

```python
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key
import base64

_PUBKEY = load_pem_public_key(NAS_PUBKEY_PEM)
def encrypt_field(plain: str) -> str:
    cipher = _PUBKEY.encrypt(plain.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(cipher).decode("ascii")
```

### 1.2 token 管理

登录成功后 NAS 给一串 token(其实是 JWT,前缀 `108MSQl...`),所有后续请求带上。token 失效码是 `N001208`,看到这个就重新登录。

并发场景下要加锁重登(避免 10 个并发请求同时看到 N001208,同时触发 10 次 login):

```python
async def _maybe_relogin(self, response_data: dict) -> bool:
    if str(response_data.get("code")) != "N001208" or not self._logged_in:
        return False
    async with self._login_lock:
        # 二次检查:可能其他请求已经完成了重登
        if str(response_data.get("code")) != "N001208":
            return False
        await self.login()
        return True
```

### 1.3 设备指纹绕过短信验证

新设备首次登录会触发 `N001414 新设备需要短信验证`。绕过方法:**复用一个已登记的 device_id**。从 NAS 的 `device.db` 只读查到 web 端已登记的设备 ID(比如 `Firefox/151.0` 那台),塞进所有请求的 query string:

```
?plat=web&version=2.3.2026062201&device_id=a6b4bd9e...&device=linux&_l=zh-CN
```

这是 axios 拦截器给所有请求追加的公共参数,server 端校验。

---

## 2. 端点挖掘的三种方法

NAS 不开放 API 文档,但所有端点都能从它自己的资源里挖出来。

### 方法 A:SSH 看 openresty 配置

```
$ ssh root@nas 'find /usr/openresty/nginx/conf/vhost -name "*.conf"'
/usr/openresty/nginx/conf/vhost/5055.conf             # 主 API
/usr/openresty/nginx/conf/vhost/8026_netdisk.conf     # 百度网盘后端
/usr/openresty/nginx/conf/vhost/8080_pcweb.conf       # 前端 UI
/usr/openresty/nginx/conf/vhost/8020_webapi.conf
...
```

每个 `.conf` 文件就是一个反代规则,看 `location` 块就知道路径模式。

### 方法 B:scp 前端 JS bundle,grep 路径(最高产)

pcweb 是个 Vue SPA,所有 API 调用都在编译后的 JS 里以字符串字面量存在。把整个 `static/js/async/` scp 下来:

```bash
sshpass -p $KEY_SSH scp -r \
  user@nas:/zspace/applications/services/pcweb/home/static/js/async/* \
  /tmp/zspace-reverse/async/
```

然后 grep 形如 `"/path"` 的字符串:

```bash
$ grep -rhoE '"/znetdisk/[a-z0-9_/]+"' /tmp/zspace-reverse/async/*.js | sort -u
"/znetdisk/auth/check"
"/znetdisk/auth/logout"
"/znetdisk/auth/token"
"/znetdisk/auth/userinfo"
"/znetdisk/autobackup/add"
"/znetdisk/autobackup/clear_fail_files"
...
```

**一秒钟摸全 32 个百度网盘端点**。

### 方法 C:strings Go 二进制

部分后端是 Go 写的(比如 `netdiskv2_server`),路由注册的 path 字符串会留在 binary 里:

```bash
$ scp user@nas:/var/appstore/pkg/cloudBackUp/znetdiskv2/netdiskv2_server /tmp/
$ strings /tmp/netdiskv2_server | grep -E 'zspace/netdisk/(controllers|service|models)' | sort -u
zspace/netdisk/controllers/api.go
zspace/netdisk/middleware/token
zspace/netdisk/service/...
```

能看出项目结构和路由分组,虽然具体路径字符串因为 Go 编译优化混在了一大坨里。

### 三种方法的产出

| 方法 | 产出 | 适用 |
|------|------|------|
| **A: openresty 配置** | 反代规则、端口、后端 socket | 找入口和后端服务 |
| **B: JS bundle grep** | 完整 API 路径列表 | **最高产,主战场** |
| **C: Go binary strings** | 项目结构、controller 分组 | 后端是 Go 时辅助 |

---

## 3. 摸到的 API 全景

按域分类,NAS 内部 API 长这样:

| 域 | 端点前缀 | 后端 | 备注 |
|----|---------|------|------|
| 文件 | `/v2/file/*` | PHP | 老接口,功能最全 |
| 影视(极影视) | `/zvideo/*` | PHP | 分类/刮削/列表 |
| 记事本 | `/v2/file/notepad/*` | PHP | location=2 是独立记事本 |
| 标签 | `/v2/label/*` | PHP | 文件↔标签关联 |
| 存储池 | `/zspool/*` | socket | 池/磁盘/SMART |
| 共享服务 | `/api/fileshare_service/*` | socket | samba/webdav/ftp/dlna |
| 监控 | `/zstatus` + SSH `/proc` | PHP + SSH | 实时性能 |
| 远程访问 | `/zrps/*` + zos 云代理 | openresty | zconnect.cn 公网入口 |
| **百度网盘** | `/znetdisk/*`(32 端点) | PHP + Go RPC | OAuth 2.0 oob |
| **其他网盘** | `/zdrive/<brand>/*` | Go RPC | 7 brand 统一接口 |
| OneDrive | `/zonedrive/*` | socket | 旧独立命名空间 |
| 天翼云盘 | `/ztianyiclub/*` | socket | 旧独立命名空间 |
| 下载 | `/downloader/*` | aria2c + qBittorrent | BT/磁链/HTTP |

### 几个关键坑

**坑 1:路径权限**。`/v2/file/list` 用 `/<pool>/my/` 直接列报 `N001411 无权限`,必须用 `/<pool>/my/data/`(用户数据在 my 子目录下,不能直接列 my 根)。

**坑 2:`file_hash` 是空的**。`/v2/file/list` 返回的每个文件都有 `file_hash` 字段,但实测对真实文件全是空字符串 —— 没法用来做精确去重。降级用 `(size, ext)` 弱指纹,文档明确标注"误报率高,需人工核对"。

**坑 3:PHP 数组语法**。`/zvideo/classification/increase` 接收的字段名是 `file_path[]`(PHP 数组),不是 `file_path`。直接传 dict,NAS 后端 PHP 解析成数组。

**坑 4:N150 性能严重受限**。这是 Intel 最低功耗 CPU 之一,并发请求会卡死宿主。所有批量操作必须:
- 单线程(不能用 `asyncio.gather` 或线程池)
- 每请求间 `time.sleep(0.1)`(100ms 节流)
- 分页 size 200(NAS 上限)
- 进度输出到 stderr,允许 Ctrl+C 中断

---

## 4. MCP 搭建:从 API 到 AI agent

有了 API,下一步是用 MCP(Model Context Protocol)暴露给 LLM。MCP 是 Anthropic 开放的协议,让 Claude/Cursor 等 LLM 客户端能直接调用外部工具。

### 4.1 架构分层

```
┌──────────────────────────────────────────────┐
│ Agent 层(Claude / Cursor / Claude Desktop)│
│   接收自然语言,决定调哪些 tool              │
└──────────────────────────────────────────────┘
                    ↓ MCP 协议(stdio)
┌──────────────────────────────────────────────┐
│ MCP Server(FastMCP)                        │
│   mcp_server/                                │
│   ├── main.py       FastMCP 实例 + 启动      │
│   ├── tools/        按域分文件                │
│   │   ├── files.py         (12 tool)         │
│   │   ├── storage.py       (8 tool)          │
│   │   ├── zvideo.py        (8 tool)          │
│   │   ├── notebook.py      (17 tool)         │
│   │   ├── znetdisk.py      (28 tool)百度网盘 │
│   │   └── ...                                │
│   └── zenith.py     远程访问代理              │
└──────────────────────────────────────────────┘
                    ↓ httpx
┌──────────────────────────────────────────────┐
│ 协议层 nas/                                  │
│   ├── auth.py     RSA 公钥 + 加密             │
│   ├── proto.py    NAS_BASE + 公共 query      │
│   └── client.py   NasClient(token 自动续)  │
└──────────────────────────────────────────────┘
                    ↓ HTTP
┌──────────────────────────────────────────────┐
│ ZSpace NAS :5055                             │
└──────────────────────────────────────────────┘
```

总共产出 **86 个 MCP tool**(40 只读 + 46 写,含 28 个百度网盘 tool)。

### 4.2 一个 tool 长什么样

最小可用的 MCP tool:

```python
from mcp_server import main as _main
from mcp_server.main import mcp
from mcp_server.perf import _to_json

@mcp.tool()
async def list_files(path: str = "/sata14/my/data/") -> str:
    """列出 NAS 目录下的文件/文件夹。路径格式:/<pool>/my/<子目录>/"""
    nas = _main.nas
    r = await nas.post("/v2/file/list", {
        "folderId": 0, "path": path, "start": 0, "num": 200,
        "sortby": "name", "order": "asc", "show_hidden": 0,
    })
    return _to_json(r)
```

LLM 看到 docstring 就知道这个 tool 干啥、参数怎么传,自己决定什么时候调。

### 4.3 关键设计:危险写操作的安全门

写工具(`remove`、`notebook_delete`、`link_folder_to_classification` 等)在 LLM 调用时,MCP 客户端会弹 UI 让用户批准。但**UI 批准只防 LLM 误调,不防 LLM 调错**。

举例:LLM 想把目录关联到极影视分类,但那个分类被用户主动禁用了(`is_enable=0`)。如果直接发请求到 NAS,关联会"成功"但实际无效。

解法是在 tool 里加**状态校验**:

```python
@mcp.tool()
async def link_folder_to_classification(classification_id: str, file_path: str) -> str:
    # 状态校验:目标分类被禁用 → 拒绝;无法校验 → 默认拒绝(fail-closed)
    list_resp = await _main.nas.post("/zvideo/classification/list", {})
    if str(list_resp.get("code")) != "200":
        return _to_json({"error": "无法校验分类状态,拒绝执行写入"})
    target = next((c for c in (list_resp.get("data") or [])
                   if c.get("id") == classification_id), None)
    if target is None:
        return _to_json({"error": f"classification_id={classification_id} 不存在"})
    if target.get("is_enable") == 0:
        return _to_json({"error": f"分类 '{target.get('name')}' 已被禁用,不接受关联"})
    return _to_json(await _main.nas.post("/zvideo/classification/increase", {
        "classification_id": classification_id,
        "file_path[]": file_path,
    }))
```

**fail-closed 原则**:无法校验时默认拒绝,宁可漏过也不误伤。

### 4.4 接入客户端

Claude Code 的 `mcp.json`:

```json
{
  "mcpServers": {
    "zspace-nas": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/mcp_server.py"],
      "env": {
        "NAS_HOST": "192.168.0.135",
        "NAS_USER": "your_phone_number",
        "NAS_PASSWORD": "your_password",
        "NAS_DEVICE_ID": "32_hex_chars_or_leave_empty"
      }
    }
  }
}
```

启动后客户端自动发现 86 个 tool,直接对话:

> 我:"帮我把昨天下载到 /sata14/my/data/下载/ 的电影归到极影视的'电影'分类"
>
> Claude:[调 list_files] → [调 list_video_classes 找分类 ID] → [调 link_folder_to_classification]
> 
> 我:"我那篇写 docker swarm 的笔记在哪"
>
> Claude:[调 notebook_search] → "找到了,在分类'技术笔记'里,标题是《docker swarm 笔记》,要打开吗?"

---

## 5. Skill:把机械活打包成"组合动作"

MCP 是单点操作,但有些场景要连续调多个 tool + 做统计判断。这种"机械活"封装成 Claude Code 的 **skill**(本质是个带触发词的 markdown + Python 脚本)。

skill 的分工:
- **Python 脚本**做"机械活"(扫文件、跑统计、读字段)
- **LLM** 做"判断"(看输出决定下一步)
- **写操作不走脚本**,统一走 MCP tool(让用户弹 UI 批准)

目前做了 4 个 skill:

| Skill | 触发词 | 干啥 |
|-------|--------|------|
| `label-manager` | "打标签 / 按标签找" | NAS 标签批量管理 |
| `media-organizer` | "极影视分类诊断 / 哪些影片分错类了" | 极影视分类只读审计 |
| `file-organizer` | "重复文件 / 孤儿文件" | 文件库只读诊断 |
| (内置 MCP) | 无 | 直接 tool 调用 |

下面 3 个 case 展开讲。

---

## Case 1:文件整理 —— file-organizer skill

### 痛点

NAS 用了 2 年,3.6 万文件,根本不知道:
- 哪些是重复的(备份过 N 次)
- 哪些是"野生"的(不在任何分类、没标签、可能是垃圾)

官方没有任何工具能查。

### 设计

只读诊断,不动 NAS。两个命令:

```
python file_organizer.py audit-duplicates [--pool NAME] [--sample N] [--min-size MB]
python file_organizer.py audit-orphans    [--pool NAME] [--output PATH]
```

**重复检测**:DFS 扫所有池的 `/<pool>/my/data/`,按指纹分组,组内 >1 个就是候选重复。

**孤儿检测**:取 `/zvideo/classification/dirs` 拿影视源目录清单,DFS 扫所有文件,每个分类成 `video` / `labeled` / `orphan`。orphan 就是"野生文件",候选整理目标。

### N150 安全策略

N150 是低功耗 CPU,扫全盘必须温柔:

```python
SLEEP_BETWEEN_PAGES = 0.1   # 100ms 节流
PAGE_SIZE = 200              # NAS 单页上限

def dfs_scan(root, ...):
    start = 0
    while True:
        resp = nas_client.post("/v2/file/list", {
            "folderId": 0, "path": root, "start": start, "num": PAGE_SIZE,
            "sortby": "name", "order": "asc", "show_hidden": 0,
        })
        # ... 处理 items ...
        if len(items) < 200:
            return
        start += 200
        time.sleep(0.1)            # ← N150 必须节流
        # 进度每 10s 打到 stderr,允许 Ctrl+C
        if time.time() - stats['last_progress'] > 10:
            print(f"[scan] scanned={stats['scanned']} groups={len(groups)}",
                  file=sys.stderr)
```

单线程、不并发、可中断。1459 部影视 + 几千文件扫一遍几分钟,不卡宿主。

### 指纹降级

理论上 `list_files` 返回 `file_hash` 字段,但实测对真实文件全是空(坑 2)。降级用 `(size, ext)` 弱指纹 —— 误报率高(同 size 同 ext 但内容不同),所以输出明确标"候选重复,需人工核对":

```json
{
  "strategy": "size+ext(weak)",
  "strategy_note": "NAS file_hash 对真实文件返回空,无法精确去重...",
  "duplicates": [
    {"fingerprint": "size:10485760,ext:mp4", "count": 3,
     "paths": ["/a.mp4", "/backup/a.mp4", "/old/a.mp4"],
     "wasted_bytes": 20971520}
  ]
}
```

### 落地流程

```
我:"看看我 NAS 上有没有重复的大文件"
Claude:[调 file-organizer audit-duplicates --min-size 100]
       → 读 stdout 摘要:"发现 12 组重复,共浪费 4.2GB"
       → "前 3 组最大的:
          1. 1.2GB mp4 ×2(路径 A、B)
          2. 800MB iso ×3(三个备份目录都有)
          ..."
我:"第 1 组的 B 路径是旧的,删掉"
Claude:[调 remove tool,MCP 客户端弹 UI 让我批]
       ✓ 删除成功
```

---

## Case 2:iPhone 备忘录 → NAS 记事本同步

### 痛点

iPhone 自带"备忘录"app 写东西很顺手,但:
- 苹果生态封闭,数据在 iCloud
- 想长期归档到 NAS,官方无解
- 第三方 app(熊掌记、Day One)要订阅

### 设计:4 步 iPhone Shortcut + 服务端转换

iPhone 用 iOS 自带的"快捷指令"(Shortcuts)抓备忘录当前选中文本,POST 到 NAS 上的一个端点 `/shortcut/notepad`。服务端做:

1. **Cocoa HTML 解析**:备忘录导出的是 Cocoa RTFD-ish HTML,样式标签一堆。剥成 `<h1>/<h2>/<h3>/<p>/<table border=1>` 干净 HTML。
2. **emoji 处理**:iOS 备忘录的 emoji 直接 UTF-8 直传,不转 HTML entity。
3. **标题去重**:用备忘录第一行作标题,跟 NAS 已有笔记比对,重名加 `(2)` `(3)` 后缀。
4. **写入 location=2 独立记事本**:NAS 记事本有多个 location(1 是系统默认,2 是用户独立),统一推到 location=2,不污染系统记事本。

### iPhone Shortcut 配置

4 个动作:
1. **获取选中文本**(备忘录 app 内分享 → Shortcut)
2. **URL 编码**
3. **POST 到** `http://<NAS_LAN_IP>:8000/shortcut/notepad`(header 带 `X-Shortcut-Key`,服务端校验)
4. **显示结果**

iOS 端 0 密钥,0 配置(除了 Shortcut URL)。

### 备用:PWA 入口

 Shortcut 不能用的场景(共享给家人、安卓手机),提供 PWA 网页 `/n`:

```
http://nas-ip:8000/n
```

打开是个表单,标题 + 富文本 body + 提交按钮。提交走同一个端点。

### 服务端关键代码

```python
@app.post("/shortcut/notepad")
async def shortcut_notepad(request: Request):
    # 1. 鉴权(header 带 shortcut key)
    key = request.headers.get("X-Shortcut-Key", "")
    if key != SHORTCUT_KEY:
        return JSONResponse({"error": "invalid key"}, status_code=403)
    
    body = await request.body()
    data = json.loads(body)
    title = data.get("title", "").strip()
    html = data.get("body", "")
    
    # 2. Cocoa HTML → 干净 HTML
    clean_html = cocoa_html_to_clean(html)
    
    # 3. 标题去重(同分类下)
    title = dedupe_title(title, classify_id=0)
    
    # 4. 写入 location=2 独立记事本
    async with get_shortcut_nas_client() as nas:
        resp = await nas.post("/v2/file/notepad/new", {
            "title": title,
            "body": clean_html,
            "classify_id": 0,
            "location": 2,
        })
    return JSONResponse(resp)
```

`_get_shortcut_nas_client` 是个独立的服务账户 client(用 env 的 NAS_USER/NAS_PASSWORD 登录,带 5 秒重登锁),跟 web session 隔离。

### 落地效果

iPhone 备忘录里写完东西 → 分享 → 点"推到 NAS" Shortcut → 1 秒后 NAS 记事本里就有了,格式干净。2 年攒了 200 多条笔记,全是这条路。

---

## Case 3:极影视分类管理 —— media-organizer skill

### 痛点

极影视(极空间自带的影视库)用一段时间会乱:
- 同一个分类下既有电影又有电视剧(自动刮削错)
- 用户自建"电影"分类 + 系统的"电影"分类重名
- 有些分类建了但没启用(`is_enable=0`),源目录还往里塞
- "frds" 这种缩写命名,刮削出来的全是错

官方 UI 只能逐个点开看,没法批量诊断。

### 设计:只读审计 + 5 个命令

```
audit-classifications   分类审计(重名/空/异常名/未启用)
audit-sources           源目录审计(不该在影视库的路径)
audit-collections       抽样审计(每个分类的 type 分布)
suggest-moves           per-collection 挪动建议
audit-all               上面四个一起跑
```

跟 file-organizer 同样的只读原则 —— skill 只找出问题,修复单独走 MCP tool 让用户弹 UI 批准。

### 关键判断

**为什么分类下有些影片不该在那**:

```python
def audit_classifications(classes):
    issues = []
    names_count = Counter(c["name"] for c in classes)
    for c in classes:
        # 重名(用户自建 + 系统同名,应该合并到系统分类)
        if names_count[c["name"]] > 1:
            issues.append(("duplicate_name", c, "重名,建议合并"))
        # 空分类
        if c["collection_count"] == 0 and c["series_count"] == 0:
            issues.append(("empty", c, "分类为空"))
        # 异常名(全英文缩写)
        if re.match(r"^[a-z]{2,8}$", c["name"], re.I):
            issues.append(("weird_name", c, "异常命名"))
        # 未启用
        if c["is_enable"] == 0:
            issues.append(("disabled", c, "分类已禁用"))
    return issues
```

**suggest-moves**(挪动建议):

```python
def suggest_moves(classes, sample_n=30):
    """抽样每个异常分类的 collection,看 type 分布,给挪向建议"""
    for c in find_weird_classes(classes):
        collections = sample_collections(c, n=sample_n)
        # type=100 电影 / 200 电视剧 / 300 综艺
        type_dist = Counter(col["type"] for col in collections)
        if type_dist.get(100, 0) > len(collections) * 0.7:
            yield {"from": c["name"], "to": "电影", "reason": "70%+ 是电影"}
        elif type_dist.get(200, 0) > len(collections) * 0.7:
            yield {"from": c["name"], "to": "电视剧", "reason": "70%+ 是电视剧"}
```

**抽样覆盖有限**:`randomlist` 每次只回 12 部,理论最多覆盖 ~150 部,对于 1459 总数是 ~10%。这是 NAS API 的限制(`series/list` count=0 是已知 bug),接受覆盖不全,在报告里标明。

### 状态校验:防 LLM 调错

LLM 看到 suggest-moves 报告"frds 里 30 部应该挪到电影",可能直接调 `link_folder_to_classification` 把目录关联到"电影"分类。但如果"电影"分类恰好被用户禁用了呢?

`link_folder_to_classification` 工具内置 fail-closed 校验(见 4.3 节),发请求前先 check 目标分类 `is_enable`。被禁用直接拒绝,不让 LLM 错误落盘。

---

## 6. 进阶:网盘备份与分享转存

最后一 case 是百度网盘集成。这是最近摸出来的,值得单独说。

### 6.1 多网盘统一架构

极空间 NAS 支持 10 个云盘:百度、阿里、夸克、115、123、移动云盘、天翼、OneDrive、Google Drive、极空间自家云盘。

API 分两套:

**老命名空间**(每网盘独立 PHP 后端):
- `/znetdisk/*` —— 百度(32 端点,最完整)
- `/zonedrive/*` —— OneDrive
- `/ztianyiclub/*` —— 天翼

**新统一接口**(Go 后端,7 brand 共用):
```
/zdrive/<brand>/{auth,file,sync,task,fail}/*
```

支持的 brand:`adrive`(阿里)、`baidu`、`kuake`(夸克)、`yun115`、`yun123`、`yun139`(移动云盘)、`gdrive`。

每个 brand 的端点模式基本一致(因为统一抽象),但 OAuth 流程各不相同:
- 百度:OAuth 2.0 oob(client_id `GTXdyMi3Q0...`)
- 115:扫码登录(`auth/qrinfo` + `auth/qrstatus`)
- GDrive:OAuth via zconnect(极空间自家 SSO 中转,避免暴露 Google client_secret 给前端)
- 阿里云盘:OAuth 入口在后端 Go 里,JS 不暴露

### 6.2 百度 OAuth oob 流程

百度 OAuth 用 `redirect_uri=oob` 模式,意思是授权完成后**百度在页面显示一串 code 给用户手动复制**,不自动回调。这是因为 client_id 是极空间官方在百度注册的,redirect_uri 必须是 oob(改了百度报 `redirect_uri_mismatch`)。

流程:

```
1. NAS 调 /znetdisk/auth/check → 返回 OAuth URL:
   https://openapi.baidu.com/oauth/2.0/authorize?
     client_id=GTXdyMi3Q0enYhpCfiaHscBRnY9ST0t6&
     redirect_uri=oob&response_type=code&scope=basic,netdisk

2. 用户浏览器打开 URL → 登录百度 → 授权 → 百度显示 code(32 字符)

3. NAS 调 /znetdisk/auth/token {app:"baidu", code:"..."} 完成 token 交换

4. token 由 NAS 后端管理,refresh_token 默认 5 年有效
```

我做了个 CLI 工具 `scripts/netdisk_login.py` 把流程打包:自动调 check 拿 URL → `webbrowser.open()` 开浏览器 → 等用户粘贴 code → 自动调 token 完成 → 验证 userinfo。**唯一绕不开的是手动复制 code 一次**(oob 强制的)。

### 6.3 分享转存:百度独有场景

百度网盘最有价值的能力不是文件管理,是**分享转存**:别人发个分享链接 → 转存到我的网盘 → 下载到 NAS。4 个 tool:

| Tool | 干啥 |
|------|------|
| `znetdisk_share_verify(url, pwd)` | 验证分享链接 + 提取码 |
| `znetdisk_share_filelist(url, pwd)` | 列分享里的文件 |
| `znetdisk_share_transfer(url, save_path)` | ⚠️ 转存到我的网盘 |
| `znetdisk_share_transfer_result(task_id)` | 查转存结果(异步) |

跟 `znetdisk_file_download` 配合,全自动落地:

```
我:"把这个百度分享下载到 NAS:https://pan.baidu.com/s/1aBc... 提取码 ab12"
Claude:
  1. znetdisk_share_verify → 验证通过,3 个文件共 5GB
  2. znetdisk_share_transfer save_path="/接收的分享/" → 转存到我的网盘
  3. znetdisk_share_transfer_result task_id=xxx → 转存完成
  4. znetdisk_file_download file_path="/接收的分享/...",save_path="/sata14/my/data/百度下载/" 
     → 拉到 NAS
```

整个过程我只发了一句话。

---

## 7. 协议规范速查

### 7.1 公共 query 参数

所有 NAS API 请求 URL 都要追加:

```
?plat=web&version=2.3.2026062201&device_id=<32_hex>&device=linux&_l=zh-CN
```

### 7.2 错误码体系

| code | 含义 | 处理 |
|------|------|------|
| `200` | 成功 | - |
| `N001208` | token 失效 | 加锁重登 |
| `N001411` | 路径无权限 | 用 `/my/data/` 不用 `/my/` |
| `N001414` | 新设备短信验证 | 复用已登记 device_id |
| `N001200` | 账号格式不对 | RSA 用错公钥(用 `pubkey` 不是 `server_pubkey`) |
| `N001013` | 百度网盘未登录 | 走 OAuth 流程 |
| `N0010002` | 参数错误 | body 字段名/类型错 |
| `N0020005` | 阿里云盘 token 失效 | 重新授权 |
| `N120019` | 分享已转存过(算成功) | - |
| `N120020` | 影视分类字段名错 | 用 `file_path[]` 不是 `file_path` |

### 7.3 form vs json

NAS 大部分端点收 `application/x-www-form-urlencoded`,不是 JSON。PHP 后端解析逻辑:

```python
# 正确:form 编码
r = await nas.post("/v2/file/list", {
    "folderId": 0, "path": "/sata14/my/data/", ...
})  # NasClient.post 内部 form-encode

# 错误:JSON
r = await nas.post("/zvideo/classification/increase", json={...})  # 报 N001212
```

PHP 数组字段名带 `[]`:`file_path[]` / `paths[]` / `ids[]`,直接传字符串 NAS 会解析成数组。

### 7.4 大响应

相册/文件列表可能返回几十 MB JSON。MCP 客户端的 stdio 通信默认 64KB 缓冲,会卡死。解决:把 reader limit 调到 4MB+:

```python
p = await asyncio.create_subprocess_exec(
    sys.executable, "mcp_server.py",
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
    limit=4 * 1024 * 1024,   # ← 必须,默认 64KB 太小
)
```

---

## 8. Takeaway

如果你也想给自己的 NAS(或其他封闭设备)做 MCP 化,几个经验:

1. **先逆向,再 MCP**。别一上来就设计 tool schema,先把 API 摸全(JS bundle grep 是最高产的方法)。
2. **API 不开放 ≠ 没法用**。前端 JS 是最完整的 API 文档,编译后的字符串字面量不会被混淆。
3. **N150 这种低功耗 CPU,并发是天敌**。所有批量操作单线程 + sleep 节流,用户会感谢你。
4. **写操作 fail-closed**。LLM 调错是必然的,工具自己要校验状态,不能盲发请求。
5. **skill 跟 MCP 分工**。skill 做"机械活"(扫文件、统计),MCP 做"单点操作",写操作永远走 MCP(让用户弹 UI 批准)。
6. **oob OAuth 绕不开**。百度/微软这种用 oob 模式的,手动复制 code 一次是底线,但后续可以 5 年免登录。
7. **协议层要 DRY**。RSA 公钥、加密函数、URL 公共参数这些要抽公共包,不要每个 tool / 每个 skill 重复(我们项目早期有 4 处重复,后期合并成 1 处)。

---

## 9. 怎么开始用

### 一键启动

```bash
git clone <repo>
cd zspace-mcp-poc
cp .env.example .env
vi .env  # 填 NAS_HOST/NAS_USER/NAS_PASSWORD/KEY_SSH

./start.sh deps        # 装 Python 依赖
./start.sh dashboard   # 启 Web Dashboard(:8000)
./start.sh mcp-cfg     # 打印 Claude Code 的 mcp.json 配置片段
```

把 `mcp-cfg` 输出粘到 `~/.config/claude-code/mcp.json`,重启 Claude Code,86 个 tool 就出现了。

### 文档索引

- `README.md` —— 项目总览 + 目录结构
- `API.md` —— NAS 全端点速查 + 字段对照 + 易踩坑(900+ 行)
- `MCP.md` —— 86 个 MCP tool 详细文档
- `docs/iphone-shortcut.md` —— iPhone 备忘录同步完整配置
- `docs/articles/2026-07-13-zspace-nas-mcp.md` —— 本文

---

## 写在最后

这个项目本身是个 PoC,代码质量不算 production-grade,但验证了一件事:**任何能逆向出 web API 的设备,都能被 AI agent 直接操作**。

NAS 只是入口。同样的方法可以用在路由器(OpenWrt)、智能音箱、扫地机器人、监控摄像头 —— 任何带 web UI 的 IoT 设备,本质上都是"协议 + 端点",LLM 都能接管。

下一步打算做:
- **阿里云盘 MCP**(等用户在 NAS UI 登录后摸 body schema)
- **NAS 日报 agent**(每天定时跑,产出 markdown 笔记总结变化)
- **Inbox watcher**(监 `_inbox/` 目录,LLM 自动分类归档)
- **RAG 语义搜索**(NAS 自带 `bigsearch` 模块,逆向它就能"自然语言找文件")

如果你也在折腾类似的事,欢迎交流。

---

*作者注:本项目仅作技术研究和个人使用,逆向 NAS API 用于自有设备,未涉及任何破解或绕过付费功能。请遵守设备使用条款。*
