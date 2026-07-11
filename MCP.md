# ZSpace NAS MCP Server 文档

把 NAS 反向工程的 HTTP API 包成 MCP tool,让 Claude Code / Cursor / Claude Desktop 等 AI 客户端能直接调用。

源文件:`mcp_server/` 包(按域拆分) · 薄入口:`mcp_server.py`(shim,兼容外部 mcp.json) · 测试桩:`README.md` 有完整握手示例。

---

## 一、配置

### 1.1 Claude Code 配置

`~/.config/claude-code/mcp.json`:

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

`NAS_DEVICE_ID` 默认借用已登记设备(避免新设备短信验证),可选。

### 1.2 环境变量

| 变量 | 必填 | 用途 |
|------|------|------|
| `NAS_HOST` | ✅ | NAS IP(默认 192.168.0.135) |
| `NAS_USER` | ✅ | 用户名(手机号) |
| `NAS_PASSWORD` | ✅ | 密码 |
| `NAS_DEVICE_ID` | 可选 | 32 字符,默认借用 Firefox/151 的 |
| `KEY_SSH` | 可选 | `perf_snapshot` tool 需要(SSH 到 NAS 读 /proc) |
| `NAS_SSH_PORT` | 可选 | SSH 端口,默认 57922 |

### 1.3 验证启动

```bash
NAS_USER=15068832031 NAS_PASSWORD=... KEY_SSH=... .venv/bin/python mcp_server.py
# 看到日志 "MCP server 'zspace-nas' starting, 58 tools registered" 即成功
```

---

## 二、通用设计原则

读和写工具**共用同一套底层**:

1. **自动登录**:`NasClient.__init__` 自动跑 RSA-PKCS1v15 + base64 登录流程,免人工干预
2. **Form-urlencoded 默认**:NAS 端点基本都是 form(不是 JSON body),`nas.post(path, dict)` 自动展开
3. **统一响应包装**:tool 返回字符串(JSON),LLM 直接读;不抛异常,把 NAS 错误码塞进返回里
4. **错误码透传**:`N001200 账号格式不对` 等 NAS 业务错误直接给到 LLM 上下文,方便诊断

### 性能约束(N150 上跑)

- **N150 CPU + 96 个服务**:并发请求会卡死宿主。所有 tool 内部用**单 httpx.AsyncClient**(连接复用),但 tool 之间**串行**(MCP 默认就是一次一个请求)
- **大响应**:相册/文件列表等可能返回几十 MB JSON,客户端 reader limit 调到 4MB+(`asyncio.subprocess` 默认 64KB 太小,会卡住)
- **SSH `perf_snapshot`**:一次 0.3 秒搞定,5 秒 LRU 缓存,不会卡 NAS

---

## 三、Tool 总览(61 个)

按 **读 / 写 / RAG** 拆。

| 类型 | 类别 | 数量 | 工具 |
|------|------|------|------|
| 读 | 📁 文件 | 4 | `list_files` `file_info` `recent_files` `file_categories` |
| 读 | 💾 存储池 | 4 | `list_storage_pools` `hardware_info` `pool_capability` `smart_report` |
| 读 | 🖥️ 监控 | 2 | `system_status` `perf_snapshot` |
| 读 | 🎬 影视 | 6 | `list_video_classes` `get_video_classification_state` `latest_movies` `suggested_movies` `random_movies` `list_video_dirs` |
| 读 | 🎵 音乐 | 1 | `list_songs` |
| 读 | 📷 相册 | 2 | `list_albums` `list_album_feeds` |
| 读 | ⬇️ 下载 | 1 | `list_downloads` |
| 读 | 🔗 分享 | 2 | `list_shares` `list_nshares` |
| 读 | 🌐 共享服务 | 4 | `samba_status` `webdav_status` `ftp_status` `dlna_status` |
| 读 | 🔍 其他 | 1 | `whoami` |
| 读 | 📒 记事本 | 8 | `notebook_list` `notebook_info` `notebook_search` `notebook_allclassify` `notebook_classifylist` `notebook_totalsize` `notebook_getconfig` `notebook_historyinfo` |
| 读 | 🌐 远程访问 | 4 | `proxy_login` `proxy_url_for_port` `proxy_fetch` `proxy_list_whitelist` |
| 读 | 🔍 语义搜索 | 3 | `semantic_search` `reindex` `index_status` |
| 写 | 📁 文件 | 7 | `mkdir` `rename` `move` `copy` `remove` `save_file_label` `delete_label` |
| 写 | 🎬 影视 | 2 | `add_video_classification` `link_folder_to_classification` |
| 写 | 📒 记事本 | 9 | `notebook_new` `notebook_modify` `notebook_delete` `notebook_pin` `notebook_updatelabel` `notebook_movenotepad` `notebook_newclassify` `notebook_deleteclassify` `notebook_updateclassify` |

> ⚠️ **写工具 18 个全部真落盘到 NAS**(MCP 客户端调用时会让用户 UI 批准)。
>
> 🛡️ **状态校验**(2026-07-01 加):`list_video_classes` 现在带 `summary`(enabled/disabled 计数 + 禁用 ID/名字),`link_folder_to_classification` 在目标禁用时**直接拒绝**,提示用户去 pcweb UI 打开再试。LLM 调错分类就不会真落 NAS。
> `remove` **不进回收站,不可逆**。
> 记事本删除:第 1 次移到 trash(classify_id=-1),第 2 次永久删除,不可恢复。
>
> 🔍 **RAG 语义搜索**(2026-07-03 加):`semantic_search` / `reindex` / `index_status` 3 个 tool,基于 bge-small-zh-v1.5 + sqlite-vec + fastembed;写工具被动增量更新索引。详见 `README.md` 的"RAG 全局语义搜索"小节。

---

## 四、读 Tool 详细

### 4.1 📁 文件 (4 个)

#### `list_files(path="/sata14/my/data/")`

列出指定目录下文件和文件夹。

- **NAS 端点**:`POST /v2/file/list`
- **参数**:
  - `path` (str,默认 `/sata14/my/data/`):路径格式 `/<pool>/my/<子目录>/`
- **返回**:`{total, items: [{name, is_dir, size, modify_time, path}, ...]}`
- **关键坑**:
  - ⚠️ **越权 N001411**:用户只能看 `/<pool>/my/...`,不能直接 `/sata14/`
  - 上限 200 项(实测分页字段没作用,超出截断)

#### `file_info(path)`

获取单个文件/文件夹详细元数据。

- **NAS 端点**:`POST /v2/file/info`
- **参数**:
  - `path` (str,必填):完整路径
- **返回**:NAS 原始 metadata(创建/修改/大小/owner/perms)

#### `recent_files()`

最近访问过的文件清单。

- **NAS 端点**:`POST /v2/recent/list`
- **返回**:实测约 **992 项**,无分页参数

#### `file_categories()`

按类型分类统计(图片/视频/文档/音频)。

- **NAS 端点**:`POST /v2/file/categories`
- **返回**:分类计数,适合"我有啥类型文件"的概览

#### `list_file_labels()`

列出 NAS 上所有用户标签(给文件打的 tag,如 docker / 课件 / 合同验收)。

- **NAS 端点**:`POST /v2/labels/alllabels`
- **返回**:`data.list[{id, label_name, created_at, updated_at, top_flag, weight}]`
- **典型用法**:先调这个看有哪些标签,然后 `save_file_label` 给文件打

---

### 4.2 💾 存储池 (4 个)

#### `list_storage_pools()`

所有存储池 + 物理磁盘概览。

- **NAS 端点**:`GET /zspool/info`
- **返回**:每个 pool 的容量/已用/可用 + 磁盘 SMART 简报 + 温度 + 健康状态
- **实测数据**(当前这台 NAS):sata14 20TB(2 块 WDC)、nvme19 500GB(Samsung)

#### `hardware_info()`

硬件槽位信息(SATA/NVMe/eSATA 各几个)。

- **NAS 端点**:`GET /zspool/hardware/info`

#### `pool_capability()`

存储池能力(是否加密 / 支持特性)。

- **NAS 端点**:`GET /zspool/capability`

#### `smart_report(sn, pool_id)`

单个磁盘 SMART 报告。

- **NAS 端点**:`POST /zspool/smart/report2`
- **参数**:
  - `sn` (str,必填):磁盘 SN,**从 `list_storage_pools` 返回里拿**
  - `pool_id` (int,必填):所属 pool id(如 14)
- **返回**:17 个 SMART 属性(加电时间/温度/坏道/CRC 错误等)
- **使用流程**:`list_storage_pools` → 拿 `(sn, pool_id)` → `smart_report`

---

### 4.3 🖥️ 监控 (2 个)

#### `system_status()`

NAS 综合状态,数据来源 `/zstatus` HTML 页(免鉴权)。

- **数据**:开机时长 / 负载 / 内存 / 磁盘使用率 / 关键服务健康 / 网络延迟
- **实现**:抓 HTML → 正则解析 → JSON 输出
- **不需要 NAS session**

#### `perf_snapshot()`

实时性能快照,通过 **SSH 读 /proc**(更准,实时)。

- **数据**:CPU 占用 / Load / 内存 / 温度 / 网络 I/O / Top 进程
- **依赖**:`KEY_SSH` 环境变量(必填)
- **性能**:一次 SSH 0.3 秒,5 秒 LRU 缓存

---

### 4.4 🎬 影视 (5 个)

#### `list_video_classes()`

极影视所有分类。

- **NAS 端点**:`POST /zvideo/classification/list`
- **返回**: `data`(NAS 原数组,每个含 `is_system` / `is_enable` / `collection_count` 等)+ `summary`(状态摘要)
- **summary 字段**:
  - `enabled_count` / `disabled_count` / `system_count` / `user_count`
  - `disabled_ids` / `disabled_names` — 被禁用的分类 UUID 和名字(挪 collection 别挪到这类)
  - `warning` — 如果有 disabled,生成一句人话提示(如 "系统内置 ['电影','电视剧'] 已被关闭")
- **典型用法**:拿到 `disabled_ids` 后,LMM 在调 `link_folder_to_classification` 前先 `get_video_classification_state(id)` 确认状态

#### `get_video_classification_state(classification_id)`

查单个极影视分类的状态(UUID → 详情)。

- **NAS 端点**:`POST /zvideo/classification/list` (本地 filter,没单独端点)
- **返回**:`ok` + `data`(原始 NAS dict) + `is_enable` + `is_system` + `warning`(若 `is_enable=0` 会警告)
- **用法**:挪 collection 或关联目录前先确认目标没被禁用

#### `latest_movies()`

极影视最新入库合集(首页"最新",20 部)。

- **NAS 端点**:`POST /zvideo/home/collection/latest`

#### `suggested_movies()`

极影视推荐合集(首页"推荐",20 部)。

- **NAS 端点**:`POST /zvideo/home/collection/suggested`

#### `random_movies()`

极影视随机推荐(12 部,每次结果不同,适合"不知道看啥")。

- **NAS 端点**:`POST /zvideo/video/randomlist`

#### `list_video_dirs()`

极影视扫描源目录(就是哪些文件夹被极影视纳管)。

- **NAS 端点**:`POST /zvideo/classification/dirs`

---

### 4.5 🎵 音乐 + 📷 相册 (3 个)

#### `list_songs()`

极音乐全部歌曲。

- **NAS 端点**:`POST /zmusic/api/v2/song/list`
- **实测**:4549 首,主要是 FLAC/DSF 高保真格式
- ⚠️ 全量返回,**响应大**(建议 MCP 客户端 limit 调高)

#### `list_albums()`

相册列表(按 type 自动分类)。

- **NAS 端点**:`POST /v2/album/albums`
- **实测**:218 个相册
- **type 编码**:
  - 40 = 来源 / 60 = 儿童 / 90 = 主题
  - 100 = 人脸 / 110 = 场景 / 120 = 节日
  - 130 = 地理 / 150 = 宠物

#### `list_album_feeds(album_id, num=20)`

列出某相册里的照片/视频。

- **NAS 端点**:`POST /v2/album/album/feeds`
- **参数**:
  - `album_id` (int,必填):从 `list_albums` 拿
  - `num` (int,默认 20)

---

### 4.6 ⬇️ 下载 + 🔗 分享 (3 个)

#### `list_downloads()`

下载任务列表(BT/HTTP/迅雷)。

- **NAS 端点**:`POST /downloader/list`
- **返回**:`{list[{id,type,downloadDir,totalSize,completeSize,isFinished,status,rateDownload,rateUpload,uri,...}], total, totalRateDownload, seedingTaskCount, ...}`

#### `list_shares()`

**外链**分享列表 + 统计。

- **NAS 端点**:`POST /v2/share/list` + `POST /v2/share/statics`(并发拉)
- **返回**:`{list: [...], statics: {总数/过期/正常/取消}}`

#### `list_nshares()`

**内部**分享(NAS 用户之间)。

- **NAS 端点**:`POST /v2/nshare/list`

---

### 4.7 🌐 共享服务 (4 个)

NAS 上跑的 Samba/WebDAV/FTP/DLNA 服务状态。全部 POST 无参。

#### `samba_status()`

Samba/SMB 服务状态(端口、guest、host_name 等)。

- **NAS 端点**:`POST /api/fileshare_service/samba/status`

#### `webdav_status()`

WebDAV 服务状态(http_port/https_port/status)。

- **NAS 端点**:`POST /api/fileshare_service/webdav/status`

#### `ftp_status()`

FTP 服务状态(port、passive 范围、guest)。

- **NAS 端点**:`POST /api/fileshare_service/ftp/status`

#### `dlna_status()`

DLNA 服务状态。

- **NAS 端点**:`POST /api/fileshare_service/dlna/status`

---

### 4.8 🔍 其他 (1 个)

#### `whoami()`

当前登录用户信息(诊断用)。

- **返回**:`{user, profile, device_id, nas_base}`
- **不调 NAS**,纯本地变量

---

### 4.9 📒 记事本 (8 个读 tool)

**独立记事本 location=2**(主菜单里平级于保险箱的那个),**不需要保险箱开启**。
location=1 是保险箱备忘录,需要先开保险箱 — 这套 tool 全部用 location=2。

关键设计要点:
- **classify_id 语义**:`0` = 全部笔记 / `>0` = 指定**叶子**分类(不递归父级)/ `-1` = 最近删除(trash)
- **笔记 → 叶子分类绑定**:note.classify_id 等于**叶子**分类的 id,不是父级。pcweb 的"分类1"父级视图是**前端聚合**(遍历树 + 每个叶子调 `notebook_list(classify_id=leaf.id)`),NAS 端没有"递归 list 父节点"的端点

#### `notebook_list(classify_id=0, num=50, start=0)`

列出笔记(支持分页)。

- **NAS 端点**:`POST /v2/file/notepad/list`
- **参数**:
  - `classify_id` (int,默认 0):0=全部 / >0=叶子分类 / -1=trash
  - `num` (int,默认 50):每页条数
  - `start` (int,默认 0):分页偏移
- **返回**:`{list[{id, title, length, in_brief, classify_id, classify_name, note_type, pin_flag, label, updated_at, ...}], total}`

#### `notebook_info(id)`

单条笔记详情(含 body HTML)。

- **NAS 端点**:`POST /v2/file/notepad/info`
- **参数**:
  - `id` (int,必填):笔记 id
- **返回**:`{id, title, body, in_brief, classify_id, label_id, note_type, pin_flag, updated_at, ...}`
- ⚠️ 列表字段叫 `label`,详情里叫 `label_id`(NAS 不一致)

#### `notebook_search(keyword, num=50)`

关键词搜索笔记(标题/正文/in_brief 全文匹配)。

- **NAS 端点**:`POST /v2/file/notepad/searchnotepad`
- **参数**:
  - `keyword` (str,必填)
  - `num` (int,默认 50)

#### `notebook_allclassify()`

完整分类树(**含嵌套,每个节点带 `child[]` 数组**)。

- **NAS 端点**:`POST /v2/file/notepad/allclassify`
- **返回**:`{list[{id, name, weight, parent_id, child: [...]}]}`
- 💡 **比 `notebook_classifylist` 更常用** — 一个调用拿到完整树,前端递归渲染

#### `notebook_classifylist()`

顶层分类列表(只列 `parent_id=0` 的顶层,带 `child_num` 计数)。

- **NAS 端点**:`POST /v2/file/notepad/classifylist`
- **不如 `notebook_allclassify` 完整**(无嵌套),只是顶层概览

#### `notebook_totalsize()`

笔记总占用大小(字节)。

- **NAS 端点**:`POST /v2/file/notepad/totalsize`

#### `notebook_getconfig()`

记事本配置(自动保存时间等)。

- **NAS 端点**:`POST /v2/file/notepad/getconfig`
- **返回**:`{list[{id, scope, config_key, config_value, ...}]}`

#### `notebook_historyinfo(id, history_id=0)`

### 4.10 🌐 远程访问代理 (4 个 tool)

**架构**:zos 给每个 NAS 内网端口分配公网子域名 `https://remote-access-{port}.zconnect.cn/`,自动代理到 NAS `127.0.0.1:{port}`(前提是白名单里有这端口)。

**认证**:`/auth/login` 返回的 `token` 跟 zenith session cookie 是 **同一个 JWT**(前缀 `108MSQl...`),可以直接用。但云代理还要求 `sign` / `cloudPubKey` / `nasPubKey` 等额外 cookie(从 `www.zconnect.cn` SSO 来,`/auth/login` 不发),所以需要从浏览器 DevTools 复制完整 cookie 串塞进 `ZENITH_COOKIE` env。

#### `proxy_login()`

强制重登录刷新 zenith session(走 `/auth/login`,不改 `ZENITH_COOKIE`)。

| 字段 | 说明 |
|------|------|
| 返回 | `{ok, user_id, username, nickname, is_master, cookie_count, has_extra_cookie}` |
| 用途 | token 过期后想用其他 `proxy_*` 工具时先调一下(虽然 `ZENITH_COOKIE` 不会自动续) |

#### `proxy_url_for_port(port)`

生成公网 URL 模板,纯计算不发请求。

| 字段 | 说明 |
|------|------|
| `port` | NAS 本地端口号 |
| 返回 | `{port, url, note}`(url = `https://remote-access-{port}.zconnect.cn/`) |
| 用途 | 让 LLM 知道给定端口对应的公网入口;给笔记/书签/MCP 调用方用 |

#### `proxy_fetch(port, path="/", method="GET", body="")`

实际通过云代理发请求。

| 字段 | 说明 |
|------|------|
| `port` | NAS 本地端口(白名单里的) |
| `path` | 请求路径,默认 `/` |
| `method` | HTTP 方法,默认 GET |
| `body` | 请求 body(POST/PUT 时用,application/x-www-form-urlencoded) |
| 返回 | `{_status, content_type, url, body}`(body 截到 4000B) |

实测:
- `proxy_fetch(33335, "/")` → 200 + TRADIS HTML(SPA)
- `proxy_fetch(33335, "/api")` → 404 + `{"error":"API not found"}`
- `proxy_fetch(80, "/")` → 403(白名单无 80)
- `proxy_fetch(9876, "/")` → 502(`192.168.0.118` 跨机,代理不通)

⚠️ 已知坑:
- 没设 `ZENITH_COOKIE` → 302 跳 `https://www.zconnect.cn/`
- cookie 过期 → 同上
- 端口不在白名单 → 403
- 跨机条目 → 502

#### `proxy_list_whitelist()`

读 NAS 远程访问白名单。

| 字段 | 说明 |
|------|------|
| 返回 | `{gap: true, msg, logged_in_as, nas_id, tip}` |
| **gap** | NAS 上 `/zrps/api/remoteaccess/{list,info,getInfo}` 全部返回 200 + 空 body(openresty 注册了路由但后端 dead)。白名单**只能从 pcweb UI 看**,此 tool 现在返回提示 + 当前登录信息 |
| 用途 | 让 LLM 知道白名单同步得手动 |

历史版本详情(从历史版本拿 body)。

- **NAS 端点**:`POST /v2/file/notepad/historyinfo`
- **参数**:
  - `id` (int,必填):笔记 id
  - `history_id` (int,默认 0):历史版本 id(`historylist` 字段未破,先传 0 也能拿到笔记的"当前版本"快照)
- ⚠️ `notebook_historylist`(历史版本列表)字段未破(NAS 一律返回 N001212),所以先不加

### 4.11 🔍 RAG 语义搜索 (3 个 tool)

把 NAS 已有内容(记事本 body + 文件名 + 白名单文本文件内容)embed 进向量库,
自然语言查询。基于 `bge-small-zh-v1.5`(中文 SOTA 小模型,512 维) + `sqlite-vec` + `fastembed`(ONNX,无 torch)。

**索引存储**:`~/.cache/zspace-rag/rag.db`(~50MB / 1000 chunks),git ignore。

**写时被动增量**:`notebook_new/modify/delete/movenotepad` 和 `mkdir/rename/move/copy/remove`
**全部自动触发后台 RAG 同步**(单条 < 1s,不阻塞主流程)。文件**内容**在 NAS 端被改的情况索引跟不上,
需要再跑一次 `reindex(scope="files", full=false)` 让 hash 没命中的重做。

#### `semantic_search(query, scope="all", top_k=10)`

自然语言搜索,返回 top_k 个最相关的 chunk。

- **参数**:
  - `query` (str,必填):自然语言,如 "docker 容器编排"、"去年写的技术笔记"
  - `scope` (str,默认 "all"):"all" / "files" / "notebooks"
  - `top_k` (int,默认 10,1-50)
- **返回**:JSON `[{source_type, source_id, source_path, snippet, score, distance}, ...]`
  - `score ∈ [0, 1]`,越大越相关(`distance` 是 `1 - score` 便于排查)
  - 索引为空时返回 `{"results": [], "hint": "先调 reindex 全量构建"}`
- **示例**:
  ```json
  semantic_search(query="docker 容器编排", top_k=3)
  → [
      {"source_type": "notebook", "source_path": "notebook:116:docker 学习笔记",
       "snippet": "docker compose 用于多容器编排", "score": 0.2474},
      ...
    ]
  ```
- **注意**:
  - 比关键词搜更宽容:同义词、错别字、口语表达都能命中
  - 范围:记事本 body + 文件名 + 文本文件内容(<100KB + 白名单扩展名);**不做**影视/相册/音乐
  - 走 sqlite-vec KNN(子查询 + LIMIT),top_k × 3 起,过滤 scope 后再截 top_k

#### `reindex(scope="all", full=False)`

后台重建索引。N150 上全量 100 条记事本约 5-10 秒,1000 文件约 1-2 分钟。

- **参数**:
  - `scope` (str,默认 "all"):"all" / "notebooks" / "files"
  - `full` (bool,默认 False):True 删旧全重建;False 只索引之前没 hash 命中的
- **返回**(立刻返回,不阻塞):
  ```json
  {"status": "started", "task_id": "all:full", "scope": "all", "full": true,
   "inflight_chunks": 0, "hint": "稍后调 index_status 看进度 / done"}
  ```
  已有同 scope+full 在跑会返回 `status: "already_running"`
- **触发时机**:
  - 第一次用:跑一次全量
  - 写时已自动增量,通常**不需要**再跑
  - 文件**内容**在 NAS 端被改:再跑一次 `full=false` 让没 hash 命中的重做
- **files 模式**:BFS 走 `/sata14/my/`(默认根,可通过代码改),max-depth=6,
  跳过 `@eaDir` / `#recycle` / `.@__thumb` 等系统目录,
  文本白名单(`.py`/`.md`/`.json`/`.yaml`/`.conf`/`.sh`/`.sql`/`.go`/`.rs`/`.c`/`.cpp`/`.html`/`.css`/`.log`/...)
  + Dockerfiles/Makefiles 才拉内容
- **notebooks 模式**:递归走 `allclassify` 拿所有叶子分类,每个叶子 `notebook_list` 分页拉笔记 id,
  再 `notebook_info` 拿 title+body,`in_brief` 字段是 NAS 解码后的 body(html)
- ⚠️ 跑时 NAS list_files / notepad/list / notepad/info 会高频调,NAS 端会卡,建议非高峰跑

#### `index_status()`

返回索引概况 + 后台任务状态。

- **返回**:
  ```json
  {
    "model": {"name": "BAAI/bge-small-zh-v1.5", "dim": 512},
    "counts": {"files": 5, "notebooks": 12, "chunks": 47,
               "db_path": "/home/corain/.cache/zspace-rag/rag.db",
               "db_size_bytes": 53248},
    "last_reindex": "all:full=true:1783000000",
    "inflight_chunks": 0,
    "reindex_tasks": {"all:full": "done (no exception)"}
  }
  ```
- **用于**:
  - 确认索引是否建好(`counts.chunks > 0`)
  - 看 reindex 是否还在跑(`reindex_tasks` 里 `running` vs `done`)
  - 看模型是否加载成功(`model.name`)

#### 已知 gap

- 文件**内容**在 NAS 端被改(没走 MCP)→ 索引跟不上,需要 `reindex(scope="files", full=false)`
- `notebook_movenotepad`(只改分类)不触发 RAG 同步(分类不在索引里)
- N150 单条 embed ~50ms;1000 文件全量索引 ~1 分钟
- 模型首次加载会下载 ~100MB ONNX + tokenizer,缓存到 `~/.cache/huggingface/`

---

## 五、写 Tool 详细(18 个,⚠️ 真落盘)

> MCP 客户端(Claude Code/Cursor)调用写 tool 时会弹 UI 让用户批准,**所以这里不做 confirm**。
> 每个 tool docstring 写清楚后果,让 LLM 知道自己在干啥。

### 5.1 📁 文件 (6 个)

#### `mkdir(parent, name)`

⚠️ 在 NAS 创建文件夹。

- **NAS 端点**:`POST /v2/file/newdir`
- **参数**:
  - `parent` (str,必填):父目录,**无尾斜杠**,如 `/sata14/my/data/备份`
  - `name` (str,必填):新文件夹名,如 `test`
- **返回**:新文件夹的完整 metadata(失败返回 NAS 错误码)

#### `rename(path, newname)`

⚠️ 重命名文件/文件夹。

- **NAS 端点**:`POST /v2/file/modify`
- **参数**:
  - `path` (str,必填):原完整路径
  - `newname` (str,必填):**只名字**,不是完整路径

#### `move(paths, to)`

⚠️ 移动文件/文件夹。

- **NAS 端点**:`POST /v2/file/move`
- **参数**:
  - `paths` (str,必填):源路径,**多个用英文逗号分隔**,如 `/a/b.txt,/c/d.txt`
  - `to` (str,必填):目标目录(必须已存在)
- **坑**:字段是 `paths[]`(PHP 数组语法),代码里自动处理

#### `copy(paths, to)`

⚠️ 复制文件/文件夹。

- **NAS 端点**:`POST /v2/file/copy`
- 同 `move`,字段 `paths[]` 自动处理

#### `remove(paths)`

⚠️⚠️ **危险,不可逆**。

- **NAS 端点**:`POST /v2/file/remove`(**不是 delete**)
- **不进回收站**,NAS 直接抹
- 字段 `paths[]` 自动处理

#### `save_file_label(label_names, paths)`

⚠️ **覆盖式写入**:把指定标签集合完整替换到这些文件上。

- **NAS 端点**:`POST /v2/labels/savefilelabel`
- `label_names`:标签名,**多个用英文逗号分隔**(如 `docker,重要`)
- `paths`:文件路径,**多个用英文逗号分隔**(如 `/sata14/my/data/a.yml,/sata14/my/data/b/`)
- 字段 `label_names[]` + `filepaths[]` PHP 数组语法,自动处理
- ⚠️ **覆盖式**:会清掉这些文件上之前已打的其他标签,不是追加
- ⚠️ **如果 label_names 里有不存在的标签名,NAS 会自动创建**(实测验证)。所以这个 tool 同时也是**创建新标签**的唯一入口 — 想要纯创建标签但不打到任何文件,传 `paths="/sata14/my/data/"`(任意已有路径即可)

#### `delete_label(label_names)`

⚠️⚠️ **危险:删除标签会让所有文件上这个标签消失**(不只是解除关联)。

- **NAS 端点**:`POST /v2/labels/deletelabel`
- `label_names`:标签名,**多个用英文逗号分隔**(如 `docker,重要`)
- 字段 `label_names[]` PHP 数组语法,自动处理
- ⚠️ 标签 ID 在 `list_file_labels` 里看,但删除**用名字**,不用先查 ID
- 标签不能有空格/特殊字符(实测中文 + 英文 ok)

### 5.2 🎬 影视 (2 个)

#### `add_video_classification(name, file_path="", not_scrape=1)`

⚠️ 在极影视新建一个分类。

- **NAS 端点**:`POST /zvideo/classification/add`
- **参数**:
  - `name` (str,必填):分类名
  - `file_path` (str,可选):关联目录路径,**实测 NAS 不会真关联**,得另外调 `link_folder_to_classification`
  - `not_scrape` (int,默认 1):1=不刮削(推荐测试用,避免 NAS 跑去 TMDB 查询);0=刮削

#### `link_folder_to_classification(classification_id, file_path)`

⚠️ 把目录**真正**关联到极影视分类。

- **NAS 端点**:`POST /zvideo/classification/increase`
- **参数**:
  - `classification_id` (str,必填):分类 UUID,从 `list_video_classes` 拿
  - `file_path` (str,必填):要关联的目录,如 `/sata14/my/data/备份/test`
- **状态校验** ⚠️ **新增**:目标分类 `is_enable=0` 时**直接拒绝**,不发请求到 NAS,返回 `error: "分类 'X' 已被禁用(is_enable=0),不接受关联"` + `hint` 让用户在 pcweb UI 打开再试
- **ID 不存在**:也直接拒绝,返回 `error: "classification_id=... 不存在"` + `hint: 调 list_video_classes 拿有效 ID`
- **坑**:字段名 `file_path[]`(PHP 数组语法,带 `[]` 后缀),代码自动处理
- **返回 `N120019`**:已经关联过,也算成功

### 5.3 📒 记事本 (9 个写 tool,⚠️ 真落盘)

> 全部用 `location=2`(独立记事本)。location=1 是保险箱备忘录,需要先开保险箱,**没暴露**(用 `notebook_*` 这个工具组)。
> 关键坑:
> - **h1 前缀**:body 必须以 `<h1>{title}</h1>` 开头(代码里**自动加**,不用手动拼,但要知道为啥)
> - **删两次坑**:同 id 第 1 次删 → 移到 trash(`classify_id=-1`);第 2 次删 → 永久删除,不可恢复
> - **叶子分类绑定**:`classify_id` 必须传**叶子** id,不能用父级 id(否则笔记"丢"到错的分类)
> - **批量删除**:传 `ids="3,4,5"` 字符串,代码自动转 `ids[]` PHP 数组

#### `notebook_new(title, body, classify_id=0)`

⚠️ 新建笔记。

- **NAS 端点**:`POST /v2/file/notepad/new`
- **参数**:
  - `title` (str,必填)
  - `body` (str,必填):HTML 正文,**自动加 h1 前缀**(不用手动拼)
  - `classify_id` (int,默认 0):目标**叶子**分类 id(0=未分类)
- **返回**:新笔记 id

#### `notebook_modify(id, title, body)`

⚠️ 修改笔记。

- **NAS 端点**:`POST /v2/file/notepad/modify`
- **参数**:
  - `id` (int,必填):笔记 id
  - `title` (str,必填):新标题
  - `body` (str,必填):新正文(自动加 h1 前缀)

#### `notebook_delete(ids)`

⚠️ 删除笔记(支持批量,**进 trash**)。

- **NAS 端点**:`POST /v2/file/notepad/delete`
- **参数**:
  - `ids` (str,必填):笔记 id,**多个用英文逗号分隔**,如 `"3,4,5"`
- **删除语义**:
  - 第 1 次删某 id → 移到 trash(`classify_id=-1`)
  - 第 2 次删同 id → **永久删除,不可恢复**
- **批量**:传 `ids="3,4,5"` 一个字符串,代码自动转 `ids[]=3&ids[]=4&ids[]=5`

#### `notebook_pin(id, pin_flag)`

⚠️ 置顶 / 取消置顶。

- **NAS 端点**:`POST /v2/file/notepad/pin`
- **参数**:
  - `id` (int,必填)
  - `pin_flag` (int,必填):1=置顶, 0=取消

#### `notebook_updatelabel(id, label)`

⚠️ 更新标签。

- **NAS 端点**:`POST /v2/file/notepad/updatelabel`
- **参数**:
  - `id` (int,必填)
  - `label` (str,必填):逗号分隔(如 `"工作,dashboard"`);**空字符串 = 清空所有标签**

#### `notebook_movenotepad(id, classify_id)`

⚠️ 移动到分类。

- **NAS 端点**:`POST /v2/file/notepad/movenotepad`
- **参数**:
  - `id` (int,必填)
  - `classify_id` (int,必填):目标**叶子**分类 id

#### `notebook_newclassify(name, parent_id=0)`

⚠️ 新建分类。

- **NAS 端点**:`POST /v2/file/notepad/newclassify`
- **参数**:
  - `name` (str,必填):分类名
  - `parent_id` (int,默认 0):父分类 id(0=顶级,>0=父分类的 id 实现嵌套)

#### `notebook_deleteclassify(classify_id)`

⚠️ 删除分类(**该分类下的笔记会被 NAS 处理**,进 trash 或变 `classify_id=0`,实测未明)。

- **NAS 端点**:`POST /v2/file/notepad/deleteclassify`

#### `notebook_updateclassify(classify_id, new_name)`

⚠️ 重命名分类。

- **NAS 端点**:`POST /v2/file/notepad/updateclassify`
- **参数**:
  - `classify_id` (int,必填)
  - `new_name` (str,必填)

---

## 六、覆盖差距 / 已知缺口(2026-06-30)

### 6.1 ✅ 已补:📒 记事本 (`/v2/file/notepad/*`)

2026-06-30 加了 **17 个 tool**(8 读 + 9 写),Dashboard `/action/notebook-*` 全部已映射到 MCP。

剩余 7 个端点**暂不加**(理由):

| 端点 | 暂不加理由 |
|------|----------|
| `notepad/downloadocx` `.docx` | 二进制返回,MCP 难表达文件保存 |
| `notepad/downloadt` `.txt` | 同上 |
| `notepad/downloadfile` 附件 | 同上,且 `file_id` 笔记详情接口不返回 |
| `notepad/uploadfile` | 上传,LLM 不擅长持有二进制 |
| `notepad/setconfig` | 配置字段未破,等抓到 HAR 再加 |
| `notepad/save_classify_tree` | 字段未破,NAS 返回 N001212 |
| `notepad/historylist` | 字段未破,NAS 一律返回 N001212 |

### 6.2 其他缺:暂缓,有需求再加

| 类别 | 端点 | 备注 |
|------|------|------|
| 加密目录 | `/v2/encryptdir/*` | 需要解锁流程 |
| 压缩/解压 | `/v2/compression/*` + `/v2/decom/*` | 长任务,需轮询 |
| 用户/权限 | `/v2/public/*` | 多账号管理 |
| Web Office | `/v2/weboffic/*` | 内部 NAS 协作 |
| 极音乐 | `/zmusic/api/v2/*` | 只暴露了 `song/list` |
| 极漫画 | (socket) | 后端未启用 |
| 邮件备份 | (socket) | 后端未启用 |
| FTP 备份 | (socket) | 后端未启用 |

### 6.3 ✅ 已补(部分):🌐 远程访问 (`/zrps/api/remoteaccess/*` + zos 云代理)

2026-07-01 加了 **4 个 proxy tool**(`proxy_login` / `proxy_url_for_port` / `proxy_fetch` / `proxy_list_whitelist`),通过 zos 云代理 `https://remote-access-{port}.zconnect.cn/` 访问 NAS LAN 服务。

**实现路径**:
- 复用 `/auth/login` 拿 `token`(直接当 zenithtoken 用,同 JWT)
- 叠加 `ZENITH_COOKIE` env 里的完整 cloud session cookie(`sign` / `cloudPubKey` / `nasPubKey` 等)
- HTTPS 请求发到 `https://remote-access-{port}.zconnect.cn/{path}`,zenith 反代到 NAS `127.0.0.1:{port}`

**仍缺**:
- `/zrps/api/remoteaccess/list` 在 NAS 上 dead(200 + 空 body),`proxy_list_whitelist` 只能返回 gap 说明 + 提示从 pcweb UI 同步
- `/zrps/api/remoteaccess/add` 真实存在(用户 curl 验证过)但**未做 MCP 写 tool** —— 加白名单是写操作且 `ZENITH_COOKIE` 模式下还要 cloud 端额外鉴权,先不做
- `/zrps/api/remoteaccess/delete` / `update` 同上,未加

### 6.3 已暴露但可能不完整

| Tool | 现状 | 可改进 |
|------|------|--------|
| `recent_files` | 实测 992 项硬上限 | 加分页 |
| `list_album_feeds` | 有 num 参数 | 加 start 支持分页 |
| `smart_report` | 只查单个磁盘 | 加批量 |

---

### 6.5 ✅ 已部署:iPhone Shortcut 推送入口(非 MCP,走 dashboard)

**端点**:`POST /shortcut/notepad`(在 dashboard `app/routes/shortcut.py` 里,不是 MCP tool)
**配套文档**:`docs/iphone-shortcut.md`
**用途**:iPhone 备忘录 → NAS 记事本 同步(单向)

**完整链路**:
1. iPhone 4 动作 Shortcut:`查找备忘录` → `从输入获取多信息文本` → `用多信息文本制作 HTML` → `获取 URL 内容 POST`
2. iOS 发的 body 是 Cocoa HTML Writer 风格完整 HTML 文档(`<!DOCTYPE>...<html>...<head><style>...<body>...</html>`)
3. 服务端 `_cocoa_html_to_clean()` 剥样式/转干净 HTML:`p.p1+span.s1` (28px 加粗) → `<h1>`,`p.p2+span.s2` (22px) → `<h2>`,`p.p3+span.s3` (17px 加粗) → `<h3>`,`p.p3+span.s4` (17px 正常) → `<p>`,`<table class="t1">` → `<table border="1">`
4. 服务端从 `<h1>` 抽 title,自动剥掉 body 里的第一个 `<h1>` 避免重复
5. emoji 走纯 UTF-8 直传(极空间 app 自带彩色 emoji 字体渲染)
6. 写 NAS `/v2/file/notepad/new` → 极空间 app 显示(标题样式 + 表格带边框 + emoji 彩色小图片)

**关键约束**:
- **iOS Shortcut 实际 Content-Type 是 `application/x-www-form-urlencoded`**,不是 text/plain(空 key 的单字段)
- iOS 18 默认禁明文 HTTP,需在**设置 → 快捷指令 → 高级 → 允许访问不安全的网站**
- 同名标题自动跳过(不覆盖,返回 `exists=true, id=已有id`)
- 标题/正文长度上限 200/500KB
- 可选 `SHORTCUT_KEY` env 设静态密钥(留空 = 开放模式,只信任 LAN)

**未做 MCP tool 的原因**:iPhone Shortcut 链路需要保持"用户 0 配置 0 密钥"的服务端自动登录 + HTML 转换,MCP tool 流程不匹配。如果 LLM 想推一条笔记到 NAS,**直接调 `notebook_new(title, body, classify_id=0)`** 即可。

**PWA 兜底**:`GET /n` 是不需 Shortcut 也不需密钥的网页表单,iPhone Safari 打开加到主屏幕就能用(标题去重规则同上)。

---

## 七、性能与并发

### 7.1 N150 限制

NAS 跑的是 **N150 CPU**(低功耗)+ 96 个服务。**并发请求会卡死宿主**。

实测:
- 2-3 路并发:还能跑
- 5+ 路:开始 timeout
- 10+ 路:NAS 自己开始 502/504

### 7.2 MCP 层做法

- **Tool 间串行**:MCP 协议本身就是一次一个请求,自然串行 ✓
- **Tool 内单 client**:`NasClient` 用一个 `httpx.AsyncClient`,连接复用 ✓
- **Tool 内串行**:`list_shares` 会并发调 `/list` + `/statics` 两路 — **实测是 2 路,够用**

### 7.3 客户端建议

- reader limit 调到 4MB+(`asyncio.subprocess` 默认 64KB 太小,`list_songs` 会卡)
- 别在循环里调 MCP,会让 NAS 累死
- 大响应工具(`list_songs`/`list_albums`/`recent_files`)考虑在 LLM 上游加 filter

---

## 八、故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 启动后无任何 tool | 登录失败,自动 retry 3 次后放弃 | 看 stderr,确认密码 + device_id |
| `N001200 账号格式不对` | RSA 用错公钥 | 用 `/zspace/system/private/pubkey` 解码后的 2048-bit PEM |
| `N001414 新设备需要短信验证` | device_id 不在 NAS 已登记列表 | 复用已登记的 device_id |
| `N001411 无权限` | path 越权 | 用户只能看 `/<pool>/my/<子目录>/`,不能直接 `/sata14/` |
| `N001212 参数有误` | 字段名错 或 JSON 而非 form | 必须 form-urlencoded + 字段名照 API.md |
| `N120020` 极影视加目录失败 | 字段名错 | 是 `file_path[]` 不是 `file_path` |
| `MCP 客户端 tool not found` | 服务没启动 | 看 stderr 日志确认登录成功 |
| `MCP 大响应卡住` | reader 缓冲区不够 | 客户端 limit 调 4MB+ |
| `zvideo/classification/increase N120019` | 已经关联过 | **算成功**,无视即可 |

---

## 九、相关文档

- **`API.md`** — NAS 全端点速查(907 行)+ 字段对照 + 易踩坑(目前只覆盖到 §6.3.2)
- **`README.md`** — Dashboard + MCP 总览 + 配置示例
- **`app/`** — Dashboard FastAPI 实现(`app/routes/notebook.py` 有 24 个 `/action/notebook-*` 写测试桩)
- **`templates/tab_*.html`** — Dashboard 5 个 tab(总览/存储池/极影视/记事本/写测试)
- **`mcp_server/`** — 本文档对应的 MCP 实现(按域拆分成 `tools/*.py`)