# ZSpace Z4Pro NAS API 速查

> 入口:`http://<nas-ip>:5055`(HTTP)或 `:5056`(HTTPS)
> 本文档基于对 pcweb JS bundle + openresty 配置 + 实测反推整理,字段名以实测为准。

---

## 0. 总览

所有业务 API 都从 openresty `:5055` 进,后端按路径前缀分发:

| 前缀 | 后端 | 端口 | 鉴权 | 备注 |
|------|------|------|------|------|
| `/auth/*` | webapi → PHP (`AuthController`) | 8070 | 无 | 登录/token 校验 |
| `/v2/*` | zfilev2(Go) | 8050 | `auth.lua` | 文件、相册、笔记 |
| `/v2/album/*` | album v2 | 8060 | `auth.lua` | 相册独立后端 |
| `/zspool/*` | zspoolv3(Go) | 8004 | `auth.lua` | 存储池(ZFS v3) |
| `/storagepool/*` | storagepool | 8003 | `auth.lua` | 旧版存储池(已弃用,跳 v3) |
| `/zvideo/*` | zvideoapi(PHP) | 8020→PHP | `auth.lua` | 极影视 |
| `/file_search/*` | file_search | 8023 | `auth.lua` | 全文搜索 |
| `/system/*` | webapi → PHP | 8020 | 仅 `127.0.0.1` 可访问(走 `/local/`) | 外部访问 404 |
| `/zstatus` | openresty lua | — | 无 | NAS 状态 HTML 页 |
| `/api/fileshare_service/*` | fileshare | — | `auth.lua` | Samba/WebDAV/FTP/DLNA |

**坑点**:`/system/*` 类接口外部一律 404。要拿系统数据,要么用 `/zstatus`(HTML),要么走 SSH 隧道在本机访问 `/local/system/...`。

---

## 1. 鉴权

### 1.1 登录:RSA 加密 + form-urlencoded

```http
POST /auth/login
Content-Type: application/x-www-form-urlencoded
```

**Form 字段**(全部必填):

| 字段 | 说明 |
|------|------|
| `username` | RSA-PKCS1v15 公钥加密 + base64,明文为 11 位手机号 |
| `password` | 同上加密,明文 ≥ 8 字符 |
| `plat` | 终端类型,白名单:`mobile` / `tv` / `pc` / `pad` / `web` |
| `device` | 任意,如 `linux` |
| `device_id` | **严格 32 字符**(可用 md5 hex) |

**公钥**(从 NAS 公开端点 `/zspace/system/private/pubkey` 获取,base64 解码后是 2048-bit PEM):
```
<RSA_PUBKEY_PEM — 从 NAS /zspace/system/private/pubkey 获取>
```

⚠️ **易踩坑**:`/zspace/system/private/server_pubkey` 是另一个 1024-bit 公钥(用于 `sign2string`/注册流程),登录用的是 `pubkey` 不是它。

**加密对应**:
- 服务端 `Plugin_Util::transDecode` = `base64_decode` + `openssl_private_decrypt`(PHP,默认 PKCS1v15)
- 客户端 = `RSA.public_encrypt(PKCS1v15)` + `base64`(Python `cryptography.hazmat.primitives.asymmetric.padding.PKCS1v15()`)

### 1.2 登录返回

```json
{
  "code": "200",
  "data": {
    "id": 1,
    "username": "<your_phone_number>",
    "nickname": "cherry",
    "is_master": 1,
    "actived": 1,
    "token": "<jwt_token>",
    "sp_perms": [{"pool": "sata14", "perm": "rw", "quota": 0}, {"pool": "nvme19", "perm": "rw"}],
    ...
  }
}
```

### 1.3 后续请求需要的 cookie

| cookie 名 | 值 |
|-----------|---|
| `token` | 登录返回的 `data.token` |
| `username` | 手机号明文 |
| `device_id` | 登录时用的 32 字符 |
| `device` | 终端名 |
| `plat` | `web` |

openresty 的 `auth.lua` 拿这些 cookie POST 到 `/auth/token` 校验,通过后把用户身份塞进 `Auth-Response` header 转给后端。

### 1.4 公共 query 参数(每个请求都加)

pcweb 的 axios 拦截器自动给所有请求 URL 追加:

```
?plat=web&version=2.3.2026062201&device_id=<32字符>&device=linux&_l=zh-CN
```

外部模拟客户端必须照加,否则后端可能拒绝。**Content-Type 默认 `application/x-www-form-urlencoded`,不是 JSON**(只有少数明确的 `requestAsJson` 接口例外)。

### 1.5 新设备短信验证

如果 `device_id` 不在 NAS 的 `device.db` 里,登录返回:
```json
{"code": "N001414", "msg": "新设备登陆，需进行安全验证。"}
```

绕开方法(只读):查 `device.db` 找该用户已登记的 device_id,直接复用。SQL:
```sql
-- 只读查询,在 NAS 上跑
sqlite3 /zspace/system/db/user.db "SELECT id FROM user WHERE username='<your_phone_number>'"
sqlite3 /zspace/system/db/device.db "SELECT did, plat, dname FROM device WHERE user_id=<id>"
```

---

## 2. 监控类

### `GET /zstatus` —— NAS 综合状态 HTML 页(免鉴权)

**返回**:HTML 页面,聚合了:开机时长、序列号、系统负载(1/5/15 分钟)、内存占用、网络延迟、各盘位使用率、关键服务健康状态、网卡状态。

**关键字段**(正则提取):
- 设备状态:`设备状态.*?(\d{4}-\d{2}-\d{2}[ \d:]+)\|([^|]+天[^|]*)\|([^|]+)` → 当前时间、开机时长、序列号
- 负载 & 内存:`负载\|内存占用\|([\d./]+)\|([\d.]+)％`
- 磁盘:`([^|]+?)\|([\d.]+)％\|(是|否)\|`
- 进程:`([^|]+?(?:服务|进程|组件|器|引擎))\|(是|否)\|(是|否)\|`

**已知限制**:`/system/diskusage3`、`/system/connections`、`/system/status` 等 JSON 接口外部都 404,只能从 NAS 本机 `127.0.0.1` 通过 `/local/system/...` 访问。

---

## 3. 存储池

| 端点 | 方法 | 用途 | 必填 body |
|------|------|------|----------|
| `/zspool/capability` | GET | 能力(如是否加密池) | — |
| `/zspool/info` | GET | **核心**:pool_list + cache_list + free_list + ext_mnt_list | — |
| `/zspool/hardware/info` | GET | 硬件槽位数 `{slot:{sata,nvme,esata}}` | — |
| `/zspool/polling` | GET | 实时状态(轻量,`dev` + `sys_pools`) | — |
| `/zspool/external/list` | GET | 外部设备列表 | — |
| `/zspool/snapshot/list` | POST | 快照列表 | `pool_id` 等 |
| `/zspool/smart/report2` | POST | SMART 报告 | 磁盘标识 |
| `/zspool/cache/hits` | GET | 缓存命中率(实测 404,可能已下线) | — |
| `/storagepool/*` | — | 旧版存储池 API,返回 `N302001 存储池已经升级到v3版本` | — |

### `/zspool/info` 返回结构(关键字段)

```json
{
  "code": "200",
  "data": {
    "esp_ver": "v3",
    "pool_list": [{
      "id": 14, "name": "sata14",
      "status": "ok",                  // ok / degraded / etc
      "pool_type": "zdr",              // zdr / single / raidz1 ...
      "protocol": "protsata",
      "total_size": 20001662697472,    // bytes
      "free_size": 14099542978560,
      "usage_size": 5902119718912,
      "encrypted": 0,
      "safe_hdd_count": 2,
      "sys_raid_status": {"status":"active","rate":-1,"remain_time":-1},
      "usr_raid_status": {"status":"active","rate":-1,"remain_time":-1},
      "disk_list": [{
        "sn": "VHGDN4JM",
        "pos": 1,                       // 槽位
        "temp": 46,                     // °C
        "model": "WDC WD101EFBX-68B0AN0",
        "dev_type": "SATA",
        "total_size": 10000831348736,
        "free_size": 6457028407296,
        "usage_size": 3543802941440,
        "health": "ok",
        "status": "ok",
        "errmsg": "正常",
        "suspected_smr": 0
      }, ...]
    }, ...],
    "cache_list": [],
    "free_list": [],
    "ext_mnt_list": []
  }
}
```

---

## 4. 文件管理(`/v2/file/*`)

### 公共 body 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `folderId` | int | 大多必填 | 0 表示根 |
| `path` | string | **必填** | 格式:`/<pool>/my/<子路径>/`,尾斜杠可有可无但建议有 |
| `start` | int | 分页用 | 起始 0 |
| `num` | int | 分页用 | 每页数量 |
| `sortby` | string | 排序 | `name` / `ctime` / `size` 等;Linux 客户端会被自动追加 `_linux` |
| `order` | string | 排序 | `asc` / `desc` |
| `show_hidden` | int | 是否显示隐藏文件 | 0 / 1 |
| `dup` | int | 去重标记 | 0 |

⚠️ **权限边界**:用户只能访问 `/<pool>/my/` 下的内容。直接访问 `/<pool>/` 或 `/<pool>/my/`(无子目录)会返回 `N001411 无权限进行此操作`。**必须 `/<pool>/my/<至少一个子目录>/`**。

### 4.1 文件夹列表

```http
POST /v2/file/list
Content-Type: application/x-www-form-urlencoded
```

```json
{
  "folderId": 0,
  "path": "/sata14/my/data/",
  "start": 0, "num": 100,
  "sortby": "name", "order": "asc",
  "show_hidden": 0
}
```

**返回**(已验证):
```json
{
  "code": "200",
  "data": {
    "total": 21,
    "info": {"name":"data","path":"/sata14/my/data","is_dir":"1", ...},
    "list": [
      {
        "name":"3D模型", "path":"/sata14/my/data/3D模型",
        "is_dir":"1", "type":"0",
        "size":"0",
        "modify_time":"1765894013",   // Unix 秒,字符串
        "change_time":"1765894013",
        "access_time":"1782258940",
        "ext":"", "ftype":"0",
        "user_id":"0", "username":"",
        "encrypted":"", "favorite":false,
        ...大量字段
      }, ...
    ]
  }
}
```

### 4.2 创建文件夹(已验证可写)

```http
POST /v2/file/newdir
```
```json
{
  "parent": "/sata14/my/data/备份",   // 父目录,无尾斜杠
  "name": "test",
  "rename": 0                          // 0=不自动重命名,1=冲突时加 (1)
}
```

返回 `data` 是新建文件夹的完整 metadata。

### 4.3 上传文件(二进制)

```http
POST /v2/file/create
Content-Type: application/octet-stream
```
- Body 是文件二进制内容
- 路径/文件名通过 URL query 或 header 传(pcweb JS 里 `apiTarget:"direct"`,会绕过 zenith 中转直连 NAS)
- 超时设置很长(`0x2540be3ff`)

上传失败原因查询:`POST /v2/file/upload/whyfail`

### 4.4 其他常用端点

| 端点 | 用途 | 关键 body 字段 | 状态 |
|------|------|----------------|------|
| `/v2/file/info` | 单文件/文件夹元数据 | `path` | ✅ 已验证 |
| `/v2/file/modify` | **改名** | `path` + **`newname`**(注意不是 `name`/`rename`) | ✅ 已验证 |
| `/v2/file/move` | 移动 | **`paths[]`**(PHP 数组语法) + **`to`**(不是 `dest`) | ✅ 已验证 |
| `/v2/file/copy` | 复制 | **`paths[]`** + **`to`** | ✅ 已验证 |
| `/v2/file/remove` | **删除**(端点名是 remove,不是 delete) | **`paths[]`** | ✅ 已验证 |
| `/v2/file/download` | 下载 | | 未测 |
| `/v2/file/hash` | 计算哈希 | | 未测 |
| `/v2/file/latest/list` | 最近访问列表 | 空 body 可用 | ✅ 通 |
| `/v2/file/categories` | 按类型统计 | 空 body 可用,返回 `{categories:{...}}` | ✅ 通 |
| `/v2/file/dwlist` | 下载任务列表 | 空 body 可用 | ✅ 通 |
| `/v2/file/empty_dir` | 找空目录 | | 未测 |
| `/v2/file/decompress/*` | 解压 | 带密码的 `setpwd` | 未测 |
| `/v2/compression/*` | 压缩 | | 未测 |
| `/v2/file/notepad/*` | 笔记(保险箱备忘录 + 独立记事本) | `location=2` 独立记事本无需保险箱;`location=1` 需保险箱开启 | 部分测 |
| `/v2/file/decrypt/download` | 加密文件下载 | | 未测 |

### 4.5 写 API 字段名易踩坑(参考 skyzhao1223/zspace-cli + 实测)

| 操作 | ❌ 想当然的字段 | ✅ 真实字段 | 备注 |
|------|---------------|-----------|------|
| newdir | `path` | **`parent`** | 不是 path! |
| 改名 | `name` / `rename` | **`newname`** | modify 端点用 |
| move/copy 目标 | `dest` / `target` / `dst` | **`to`** | |
| move/copy/remove 多个源 | `paths` | **`paths[]`** | PHP 数组语法,跟 classification/increase 的 `file_path[]` 一致 |
| 删除端点 | `/v2/file/delete` | **`/v2/file/remove`** | 端点名也反直觉 |

错误码 `N007008 文件夹不存在` = `to` 路径不存在(我之前误以为是字段名错)。
错误码 `N001212 参数有误` = 字段名错或 content-type 不是 form。

---

## 5. 极影视(`/zvideo/*`)

服务端口实际在 `:8111`(zvideoapi PHP),通过 openresty 5055 反代。

### 5.1 分类(已验证)

| 端点 | 用途 | 关键 body |
|------|------|----------|
| `/zvideo/classification/list` | 列出所有分类 | `{}` — 元素含 `is_system`(1=系统内置,0=用户)、`is_enable`(1=开,0=用户主动关)、`collection_count`、`series_count`、`id`(UUID) |
| `/zvideo/classification/dirs` | 列出影视库源目录(所有分类的汇总,无分类维度) | `{}` |
| `/zvideo/classification/mode` | 当前模式(按分类/按文件夹) | `{}` |
| `/zvideo/classification/add` | **新建分类** | `classification_name` + `file_path`(可选,实测不真的关联) + `share_users`(JSON 字符串) + `not_scrape`(0/1) |
| `/zvideo/classification/increase` | **把目录关联到分类** | `classification_id` + **`file_path[]`**(注意方括号,PHP 数组语法!多个目录就重复 `file_path[]=path1&file_path[]=path2`) |
| `/zvideo/classification/del` | 删除分类 | `classification_id` |
| `/zvideo/classification/rmdir` | 从分类移除目录 | `classification_id` + `file_path` |
| `/zvideo/classification/editname` | 改名 | |
| `/zvideo/classification/rescan` | 触发重新扫描 | `classification_id` → 返回 `task_id` |
| `/zvideo/classification/checkaddstatus` | 是否有添加任务在进行 | `classification_id` |

⚠️ **`/classification/increase` 易踩坑**:
- 字段名是 `file_path[]`,**不是** `file_path`(PHP 数组语法)
- 错误码 `N120019 = 文件夹已添加,请勿重复添加`(成功后再次调用返回这个)
- 错误码 `N120020` = 参数有误(常见于用错字段名)
- 公共 query `plat/version/device_id/device/_l` 必须都带

**分类对象字段**:
```json
{
  "id": "c954fc4d-be9e-4c9a-baf7-8246b60319e1",   // UUID
  "name": "动画",
  "is_system": 0,                                   // 1=系统内置(电影/电视剧),0=用户自建
  "is_enable": 1,                                   // 1=启用,0=禁用
  "not_scrape": 0,                                  // 1=不刮削
  "auto_series": 1,
  "series_count": 17,                               // 剧集(整剧)数
  "collection_count": 36,                           // 合集(单电影/单剧集)数
  "share_users": ["18368083701", "13456961170"],
  "ext_dev": false
}
```

### 5.2 影片/合集

| 端点 | 用途 | 关键 body |
|------|------|----------|
| `/zvideo/series/list` | 某分类下的剧集列表 | `classification_id` |
| `/zvideo/series/collection/list` | 某剧集的合集列表 | `series_id`(可能) |
| `/zvideo/collection/info` | 单个合集详情 | `id` |
| `/zvideo/collection/filter` | 过滤选项(类型/地区) | `{}` 返回 `genres`/`regions` |
| `/zvideo/collection/filter/v2` | 过滤选项 v2 | `{}` |
| `/zvideo/collection/episode` | 某合集的剧集列表 | |
| `/zvideo/collection/add` | 加入影视库 | |
| `/zvideo/collection/del` | 删合集 | |
| `/zvideo/collection/seen/add` `/delete` | 标记已看 | |
| `/zvideo/share/v4/list` | 分享列表 | `{}` |

⚠️ 影片明细接口参数没完全摸清,实测 `series/list` 带 `classification_id` 返回了 `count` 但 `list` 是空 —— 可能还需要 `start`/`num` + 某个 type 字段。需要时再深挖。

### 5.3 影视发现 / 浏览类(实测 2026-06-25 新增)

| 端点 | 用途 | 实测 |
|------|------|------|
| `/zvideo/home/collection/latest` | **最新入库合集**(首页"最新") | ✅ list[20],字段 `{title, classification_id/name, collection_id, cover, backdrop, logo, release_year, score, type, extend_type}` |
| `/zvideo/home/collection/suggested` | **推荐合集**(首页"推荐") | ✅ list[20],字段同上 |
| `/zvideo/home/share/collection/latest` | 分享合集 | ✅ list[0](未分享) |
| `/zvideo/video/randomlist` | **随机推荐** | ✅ list[12],实测本机有"哈里·布朗/小妇人/老友记"等 |
| `/zvideo/video/v2/lately` | 最近观看 | ✅ `{add_task, count, list}` |
| `/zvideo/video/v2/playlist` | 播放列表 | ✅ `{count, list}` |
| `/zvideo/favorite/list` | 我的收藏 | ✅ `{count, list}` |
| `/zvideo/collection/filter` | 类型/地区过滤选项 | ✅ `{genres[22], regions, system}`(类型: 剧情/喜剧/...) |
| `/zvideo/skip/task/list` | 片头/片尾跳过任务 | ✅ `{count, list, processed, running, success}` |
| `/zvideo/task/cron/info` | 定时任务配置 | ✅ `{enable, is_cron, task_id, timer:"01:00"}` |
| `/zvideo/emby/user/status` | Emby 用户状态 | 需 `username` |

> **影片数据样例**(实测当前 NAS):
> - 哈里·布朗 (2009) 7.2 / 高压电 (2003) 6.7 / 小妇人 (1994) 7.3
> - 自由之声 (2023) 8.0 / 千谎百计 S1 (2009) 8.0
> - 老友记 S4 (1997) **8.9** / 维京传奇 S5 (2017) 7.9
>
> 这些端点直通可用,做 MCP `mcp_zspace_movie_recommend` / `mcp_zspace_movie_latest` 很合适。

---

## 6. 其他 `/v2/*` 端点(系统扫描发现)

> 通过 pcweb JS 静态分析 + 实测得到,本节列出**已确认可在 LAN 调通**的端点。

### 6.1 文件分享 `/v2/share/*`(外链分享)

| 端点 | 用途 | 关键 body |
|------|------|----------|
| `/v2/share/list` | 我创建的外链分享列表 | `{}` 返回 `list[{id, code, pass, fname, dirname, ...}]` |
| `/v2/share/statics` | 分享统计 | `{}` 返回 `{total, expired, normal, cancel, disable, other}` |
| `/v2/share/create` | 创建分享 | 待测 |
| `/v2/share/delete` | 删除分享 | `id` 或 `code` |
| `/v2/share/modify` | 改分享(密码/有效期) | `id` |

### 6.2 内部分享 `/v2/nshare/*`(用户间分享)

| 端点 | 用途 | 状态 |
|------|------|------|
| `/v2/nshare/list` | 全部内部分享 | ✅ 返回 `list[{name, path, is_dir, ...}]` |
| `/v2/nshare/mine` | 我分享出去的 | ✅ |
| `/v2/nshare/forme` | 分享给我的 | ✅ |
| `/v2/nshare/create` | 创建 | 待测 |
| `/v2/nshare/info` | 详情 | 待测 |
| `/v2/nshare/cancel` / `discard` / `make` | 待测 | |

### 6.3 最近访问 `/v2/recent/*`

| 端点 | 用途 | 备注 |
|------|------|------|
| `/v2/recent/list` | 最近访问的文件(实测 992 项) | ✅ `{}` 返回 `{total, list[{name, path, ...}]}` |
| `/v2/recent/new` | 待测 | N001411 无权限(可能有特殊要求) |
| `/v2/recent/remove` | 清除记录 | ✅ 200(写操作) |

### 6.3.1 跨设备备份 `/v2/crossdevice/backup/*`

| 端点 | 用途 | 备注 |
|------|------|------|
| `/v2/crossdevice/backup/list` | 跨设备备份列表 | ✅ `{list, total}`(本机为空) |
| `/v2/crossdevice/backup/add` / `delete` / `update` / `start` | 备份任务 CRUD | 写 |

### 6.3.2 笔记 `/v2/file/notepad/*`

NAS 里**两套笔记数据**,端点完全相同,靠 `location` 参数区分:

| `location` | 含义 | 入口 | 启用条件 |
|------------|------|------|---------|
| `1`(默认) | **保险箱**里的备忘录 | 保险箱 → 备忘录 tab | ⚠️ 需保险箱开启;否则 `N001603 保险箱未打开` |
| `2` | **独立记事本应用**(平级于保险箱) | 主菜单 → 记事本 | ✅ 不需要保险箱,直接用 |

> **坑**:不带 `location` 或 `location=1` 在没开保险箱时一律 N001603。
> 用 `location=2` 才能调到独立记事本。
>
> HAR 抓包确认:pcweb 的 `/home/notebook/...` 路由所有 `notepad/*` 请求都带 `location=2`。
> 编辑器路由:`/home/notebook/notebookEditor?id=N&replace=1&classId=0`(replace=1 表示替换模式)

`classify_id` 语义(实测 + HAR):
- `classify_id=0` → "全部笔记"(active + 未分类聚合,`classify_name="全部"`)
- `classify_id>0` → 指定分类 id(只能**直属**该分类,不会递归子分类;要看子分类下的笔记需先 `classifylist` 取子 id)
- `classify_id=-1` → "**最近删除**"(trash;NAS 内部把所有软删的笔记统一丢进 -1 这个特殊分类,**没有独立的 recycle/recentdelete 端点**)
- 其他值(`-2/-99` 等实测过):返回 0,不会列别的

**核心发现(trash 怎么列)**: pcweb 的"最近删除"视图就是 `notepad/list?classify_id=-1`,不是某个神秘端点。
往 `notebook-delete` 写 `ids[]` 即软删(进 trash);再调一次同 id 硬删(不可恢复)。批量用 `ids[]=N&ids[]=M`(PHP 数组,带 `s`,不是 `id[]`)。

| 端点 | 用途 |
|------|------|
| `/v2/file/notepad/list` | 笔记列表(`start/num/classify_id/location`) |
| `/v2/file/notepad/info` / `new` / `modify` / `delete` | CRUD(`location`) |
| `/v2/file/notepad/classifylist` / `allclassify` / `newclassify` / `deleteclassify` / `updateclassify` | 分类管理(allclassify=含嵌套,updateclassify=重命名,均带 `location`) |

**分类嵌套(实测)**:
- `classifylist` 默认只列顶层(`parent_id=0`,带 `child_num`);带 `parent_id=N` 列 N 的直接子节点
- `allclassify` 返回完整树,**每个节点带 `child[]` 数组**,前端递归聚合即可
- **笔记 → 叶子分类绑定**:note 的 `classify_id` 等于它所在最小分类的 id(子分类优先,不是父级)
  - 例:note 在 分类11 视觉目录下,实测 `classify_id=5`(分类11的 id),**不是父级 3**
  - 所以"父分类下所有笔记"视图,pcweb 是前端聚合:树遍历 + 每个叶子节点调 `list?classify_id=leaf.id`
  - NAS 端没有"递归 list 父节点下的笔记"端点
- 新建笔记想进 分类11:`new` 时把 `classify_id` 设为 5,不能设为父级 3
| `/v2/file/notepad/save_classify_tree` | 保存分类树(拖拽排序,`location`) |
| `/v2/file/notepad/historylist` / `historyinfo` | 历史版本(`location`);`historylist` ⚠️ 字段名未破(测 id/note_id/nid 全 N001212),需 pcweb HAR 补 |
| `/v2/file/notepad/getconfig` / `setconfig` | 配置读写(`location`);`setconfig` body 是完整配置 JSON |
| `/v2/file/notepad/totalsize` | 笔记总占用大小(`location`) |
| `/v2/file/notepad/searchnotepad` | 搜索笔记(`keyword/location`) |
| `/v2/file/notepad/movenotepad` | 移动笔记到分类(`id/classify_id/location`) |
| `/v2/file/notepad/pin` | 置顶/钉选(`id/pin_flag/location`;前端用 `is_top` 自动转) |
| `/v2/file/notepad/updatelabel` | 更新笔记标签(`id/label/location`;`label=""` 清空) |
| `/v2/file/notepad/uploadfile` | 上传笔记内嵌附件/图片(`POST`,`multipart/form-data` 字段名 `file`,带 `location`) |
| `/v2/file/notepad/downloadfile` | 下载笔记附件(`GET`,URL 拼 `?file_id=&location=`) |
| `/v2/file/notepad/downloadocx` | 下载 Word 导出(`GET`,URL 拼 `?id=&location=`) |
| `/v2/file/notepad/downloadt` | 下载纯文本导出(`GET`,URL 拼 `?id=&location=`) |

**`/action/notebook-*` 全部 24 个端点(dashboard 完整映射,MCP 友好)**:

读(GET,JSON 返回):
- `notebook-list` → notepad/list(支持 `start` 分页)
- `notebook-info` → notepad/info(`id`)
- `notebook-search` → notepad/searchnotepad(`keyword`);别名 `notebook-searchnotepad`
- `notebook-getconfig` → notepad/getconfig
- `notebook-totalsize` → notepad/totalsize
- `notebook-classifylist` → notepad/classifylist
- `notebook-allclassify` → notepad/allclassify(嵌套树)
- `notebook-historylist` → notepad/historylist(⚠️ 字段未破,N001212)
- `notebook-historyinfo` → notepad/historyinfo(`id` + 可选 `history_id`)
- `notebook-downloadfile` → notepad/downloadfile(二进制,`file_id`)
- `notebook-downloadocx` → notepad/downloadocx(二进制 .docx,`id`)
- `notebook-downloadt` → notepad/downloadt(二进制 .txt,`id`)

写(POST,form-urlencoded):
- `notebook-new` → notepad/new(`title` + `body` 带 `<h1>` 前缀 + `classify_id`)
- `notebook-modify` → notepad/modify(`id` + `title` + `body` 带 `<h1>` 前缀)
- `notebook-delete` / `notebook-delete-batch` → notepad/delete(`ids[]` PHP 数组语法)
- `notebook-pin` → notepad/pin(`id` + `pin_flag` 1/0;前端表单用 `is_top` 自动转)
- `notebook-updatelabel` → notepad/updatelabel(`id` + `label`)
- `notebook-movenotepad` → notepad/movenotepad(`id` + `classify_id`,leaf id)
- `notebook-newclassify` → notepad/newclassify(`name` + `parent_id` 0=顶级)
- `notebook-deleteclassify` → notepad/deleteclassify(`classify_id`)
- `notebook-updateclassify` → notepad/updateclassify(`classify_id` + `new_name`)
- `notebook-setconfig` → notepad/setconfig(请求体传完整配置 JSON)
- `notebook-save-classify-tree` → notepad/save_classify_tree(`tree` 字段为完整树 JSON 字符串)
- `notebook-uploadfile` → notepad/uploadfile(multipart `file` 字段 + `location`)

### 6.4 用户/权限 `/v2/public/*`(多账号/子账号管理)

⚠️ 这些都需要参数,字段名还没完全摸清。从路径推断用途:

| 端点 | 推断用途 |
|------|---------|
| `/v2/public/group/list` | 用户组列表(主账号管理子账号分组) |
| `/v2/public/group/add` / `delete` | 加/删组 |
| `/v2/public/permission/list` / `get` / `set` / `switch` | 权限管理 |
| `/v2/public/quota/list` / `set` | 配额管理 |
| `/v2/public/recycle/clean` / `restore` | 公共回收站 |
| `/v2/public/user/groups` | 用户的组(N001217 此子帐号不存在 → 主账号调用报错) |

### 6.5 加密目录 `/v2/encryptdir/*`

| 端点 | 用途 |
|------|------|
| `/v2/encryptdir/new` | 创建加密目录(需 path + password) |
| `/v2/encryptdir/lock` / `unlock` | 锁定/解锁 |
| `/v2/encryptdir/release` | 释放 |
| `/v2/encryptdir/rename` | 改名 |
| `/v2/encryptdir/resetpasswd` | 重置密码 |
| `/v2/encryptdir/seticon` | 改图标 |

### 6.6 压缩/解压 `/v2/compression/*` + `/v2/decom/*`

| 端点 | 用途 |
|------|------|
| `/v2/compression/browser` | 浏览压缩包内容(需 path) |
| `/v2/compression/create` | 创建压缩包 |
| `/v2/compression/download` | 下载压缩包 |
| `/v2/decom/create` | 解压 |

### 6.7 相册 `/v2/album/*`(146 个端点,极空间大头)

| 端点 | 用途 | 状态 |
|------|------|------|
| `/v2/album/albums` | 全部相册(实测 218 个) | ✅ `{}` 返回 `{total, list[{id, name, type, cover, ...}]}` |
| `/v2/album/home` | 相册首页(聚合) | ✅ 返回 `{sys_dir, ilike, albums, others, combine}` |
| `/v2/album/dirs` | 相册源目录 | ✅ |
| `/v2/album/position` | 照片位置(地图) | 需参数 |
| `/v2/album/album/create` / `delete` / `change` | 相册 CRUD | 写 |
| `/v2/album/album/feed/add` / `delete` / `move` | 相册内照片管理 | 写 |
| `/v2/album/album/comments/*` | 相册评论 |  |
| `/v2/album/ai/*` | AI 功能(人脸/宠物/OCR) | 见 6.7.1 |
| `/v2/album/ilikelist` | 我喜欢的 | (路径可能不对,404) |
| `/v2/album/share` / `nasshare` | 相册分享 |  |

#### 6.7.1 AI 子模块 `/v2/album/ai/*`(23 个端点,实测可用)

> 极空间相册 AI 功能很完整:人脸识别 / 宠物识别 / 场景识别 / OCR / 自动剪辑 / "今日回忆"配乐。
> 实测当前 NAS 状态:`face_total=2`、`pet_face_total=17364`、`scene_total=2`,识别到的人脸有用户命名(如 "温柔岁月")。

**✅ 只读 / 200 直通**:

| 端点 | 用途 | 实测返回字段 |
|------|------|--------------|
| `/v2/album/ai/state` | AI 整体状态 | `{state, face_total, face_video_total, scene_total, clip_total, pet_total, pet_count, pet_face_total, pet_face_count, ...}` ⚠️ 见下方字段解释 |
| `/v2/album/ai/taskManager` | 任务管理器 | `{aiDurationList, currentTask, lastTaskEvent, typeMap}`;`typeMap` 列出任务类型:`clip/face/ocr/pet/scene/system` |
| `/v2/album/ai/taskEventList` | 任务事件流水(实测 1000 项) | `[{userName, taskName, remark, eventId, eventTime}]` |
| `/v2/album/ai/history/today` | 今日回忆(配音乐) | `{persons, group, addr, desc, music}`;`desc.persons` 是识别出的人脸名,`music` 是配乐 URL |
| `/v2/album/ai/cluster_detect/options` | 聚类灵敏度 | `[{desc:"普通",value:0.54}, {desc:"增强",0.57}, {desc:"超级增强",0.59}]` |
| `/v2/album/ai/picking/menu_bar` | AI 挑选菜单 | `[{name:"整体评分",type:9}, {name:"人像挑选",1}, {name:"景物图片",10}, {name:"疑似瑕疵",11}]` |
| `/v2/album/ai/picking/task/status` | 当前挑选任务状态 | `{id, type, status, total, processed, remaining, msg}`;status=4 表示 cancelled |

> ⚠️ **state 字段解释容易错**(2026-06-25 实测纠正):
> - `face_total: 2` ≠ 人脸总数。实测 `/v2/album/albums` type=100 有 **129 个人脸 album**(每个识别到的人一个),`face_total` 更可能是**已命名的人脸数**
> - `pet_total: 2` ✅ 跟实测对上(type=150 "美短" album fnum=2,即 **2 张宠物照片**)
> - `pet_face_total: 17364` ❌ **不是"宠物脸数"**!只有 2 张宠物照片,远不到 17364。推测是**累计扫描的图片数**或**累计候选框数**(模型推理统计),非真实宠物脸数
> - `pet_count` / `pet_face_count` 是当前批次的处理数(0 表示 AI 已完成,不在处理中)
>
> 真实的相册分类请走 `/v2/album/albums` 按 `type` 字段聚合:
>
> | type | 含义 | 实测本机 |
> |------|------|---------|
> | 40 | 来源目录 | 16 个(WeiXin 等) |
> | 60 | **儿童相册**(有 gender/birthday 字段) | 1 个("小臭宝",男孩,2019-11-13 生,7097 张) |
> | 90 | 主题相册(用户自建) | 1 个("家装时刻") |
> | 100 | **人脸**(AI 聚类,大多未命名) | 129 个 |
> | 110 | 场景/事物 | 43 个(船 等) |
> | 120 | 节日 | 10 个(七夕情人节、春节 等) |
> | 130 | 地理位置 | 17 个(三亚市 等) |
> | 150 | **宠物** | 1 个("美短" fnum=2 ✓) |
>
> ⚠️ **关于 `/history/today` 的 `desc.persons` 字段**:
> 实测 `album_id: 1151` 对应的相册 `name: ""`(空),`type: 100`。`desc.persons: ["温柔岁月"]` **不是人脸名**,是 AI 自动生成的**回忆卡片标题**(诗意命名,如"温柔岁月")。完整结构:
> - `persons`: 数组,每项 = `{total, album_id, list[照片]}` —— 今天历史上拍的照片按"人物主题"分组
> - `desc.persons/group/addr`: AI 给三组卡片起的**诗意标题**(不是真实姓名)
> - `music.persons/group/addr`: 每组卡片配的背景音乐 URL

#### 6.7.2 单相册操作 `/v2/album/album/*`(30+ 端点)

> 单个相册的 CRUD + 内部照片管理 + 社交功能。统一用 **`album_id`** 作参数(不是 `id` 也不是 `aid`)。

| 端点 | 用途 | 关键 body |
|------|------|----------|
| `/v2/album/album/info` | 相册详情(含 owner/users/gender/birthday 儿童专属字段) | `album_id` |
| `/v2/album/album/feeds` | **列相册内照片**(分页) | `album_id` + `start` + `num`;返回 `{total, list[{id, name, path, size, width, height, ftype, crtime, longitude, latitude, faces, ...}]}` |
| `/v2/album/album/create` / `delete` / `modify` / `change` | 相册 CRUD | 写 |
| `/v2/album/album/merge` | 合并相册 | 写 |
| `/v2/album/album/feed/add` / `delete` / `move` / `kick` | 照片加入/移出相册 | 写 |
| `/v2/album/album/comments/create` / `delete` / `list` | 评论 |  |
| `/v2/album/album/posting/create` / `delete` / `info` / `home` / `like` / `list` | 贴子/动态(相册内社交) |  |
| `/v2/album/album/moments` | 时刻(回忆) | 需 `album_id` |
| `/v2/album/album/music` | 背景音乐 | 需 `album_id` |
| `/v2/album/album/download` | 打包下载 |  |
| `/v2/album/album/binds` | 绑定(分享给谁) |  |
| `/v2/album/album/flush` | 刷新 |  |
| `/v2/album/album/ai/face` / `ai/role` | 取 AI 封面(GET!)`/ai/role` 需 `album_id` |  |
| `/v2/album/album/albums/by_feed_id` | 按照片反查所属相册 | `feed_id` |
| `/v2/album/album/path/cover` | 自定义封面 |  |
| `/v2/album/album/create/batch` | 批量建相册 | 写 |

**单照片字段(`/feeds` 返回的)**:
- `ftype`: 101=照片, 102=视频
- `crtime`: 创建时间(Unix 秒)
- `cdate`: 创建日期 YYYYMMDD
- `longitude` / `latitude` / `geo_hash`: GPS
- `make` / `model`: 拍摄设备(如 apple / iphone 13)
- `faces`: `[{face_x, face_y, face_w, face_h, score}]` 人脸框坐标 + 置信度
- `ilike`: 0/1 是否点赞
- `is_livep`: Live Photo
- `is_shot`: 截图
- `is_self`: 自拍
- `is_wide`: 全景
- `duration`: 视频时长(秒)| `/v2/album/ai/taskManager` | 任务管理器 | `{aiDurationList, currentTask, lastTaskEvent, typeMap}`;`typeMap` 列出任务类型:`clip/face/ocr/pet/scene/system` |
| `/v2/album/ai/taskEventList` | 任务事件流水(实测 1000 项) | `[{userName, taskName, remark, eventId, eventTime}]` |
| `/v2/album/ai/history/today` | 今日回忆(配音乐) | 见上方解释 |
| `/v2/album/ai/cluster_detect/options` | 聚类灵敏度 | `[{desc:"普通",value:0.54}, {desc:"增强",0.57}, {desc:"超级增强",0.59}]` |
| `/v2/album/ai/picking/menu_bar` | AI 挑选菜单 | `[{name:"整体评分",type:9}, {name:"人像挑选",1}, {name:"景物图片",10}, {name:"疑似瑕疵",11}]` |
| `/v2/album/ai/picking/task/status` | 当前挑选任务状态 | `{id, type, status, total, processed, remaining, msg}`;status=4 表示 cancelled |

**⚠️ 端点存在,需参数(字段名待抓包)**:

- `/v2/album/ai/progress` — 进度查询(`type` 字段不对,需抓包)
- `/v2/album/ai/ocr/search` — OCR 文字搜索(`keyword/kw/q/word` 都不是)
- `/v2/album/ai/picking/result` — 挑选结果(需 task_id)
- `/v2/album/ai/history/today/persons/not_display` — 不显示的人物列表

**⚠️ 业务空闲(端点对,当前没任务)**:`clean/status` / `pet/clean/status` / `rescene/status` 都返回 `N003588 暂无清理任务`

**🔴 写操作(空 body 看校验,不实际触发)**:

- `/v2/album/ai/clean` / `pet/clean` — 触发清理(截图去重等)
- `/v2/album/ai/recluster` — 重新聚类人脸
- `/v2/album/ai/rescene` — 重新场景化
- `/v2/album/ai/run` / `run/now` — 触发 AI 跑
- `/v2/album/ai/picking/task/create` / `cancel` / `completed` — 挑选任务 CRUD

**typeMap 数字编码**(从 taskManager 拿到):
- `clip` → 140(自动剪辑)
- `face` → 100 / 1000(人脸识别,可能 100=检测,1000=聚类)
- `ocr` → 1200(图片文字识别)
- `pet` → 150(宠物识别)
- `scene` → 110(场景识别)
- `system` → [](系统任务)

### 6.8 Web Office `/v2/weboffic/*` + `/v2/onlyoffice/*`

| 端点 | 用途 |
|------|------|
| `/v2/weboffic/getconfig` | Web Office 配置(status, version_num, app_id, ...) |
| `/v2/weboffic/saveconfig` | 保存配置 |
| `/v2/onlyoffice/font/list` | 字体列表 |
| `/v2/onlyoffice/font/save` / `copy` / `task` | 字体管理 |
| `/v2/onlyoffice/file/rename` | Office 文件改名 |

### 6.9 404 的端点(后端不在 8050)

以下端点在 openresty 路由表里,但实际调用 404 —— 推测后端在别的服务/未启用:

- `/v2/email/*`(check, modify, send) — 邮件相关
- `/v2/qc/*`(create/master, list, ...) — 不明
- `/v2/ud/*`(check, expire, key/pub) — 不明(可能 user device)
- `/v2/short/create` — 短链?
- `/v2/captcha/` — 验证码
- `/v2/tob/share/config/get` — 不明

### 6.10 极音乐 `/zmusic/api/v2/*`(zmusicv2 后端,unix socket)

| 端点 | 用途 | 状态 |
|------|------|------|
| `/zmusic/api/v2/song/list` | 全部歌曲列表 | ✅ 实测 **4549 首**,每首含 song_id/title/artist/album/cover/duration/size 等 15+ 字段,以 FLAC/DSF 高保真格式为主 |
| `/zmusic/api/v2/song/file/share` | 分享歌曲 | 需参数(400) |
| `/zmusic/api/v2/setting` | 设置 | 需参数(400) |
| `/zmusic/api/v2/album/*` `/artist/*` `/playlist/*` `/favorite` `/recent` | 其他常见音乐端点 | ❌ 404,可能 v2 API 表面就这么窄,或藏在子路径 |

**实测单首歌字段**:
```
song_id, song_name (文件名), song_title, artist, artist_list[{artist_id, name, profile}],
album, album_id, album_artist, song_cover, song_date, song_genre,
song_disc, song_track, song_size, song_duration
```

### 6.11 下载 `/downloader/*` + `/xunlei/*` 等(后端按需启)

⚠️ **当前用户没启用下载功能,所有路径 502**(unix socket 文件不存在)。

| 路径前缀 | 后端 | 备注 |
|---------|------|------|
| `/downloader/` | downloader.socket | openresty 转发到 unix socket,502 fallback 到 127.0.0.1:8001 |
| `/xunlei/` | 127.0.0.1:5052 | 迅雷下载 |
| qbittorrent | 58082(openresty `58082_qbittorrent.conf`) | BT 下载 |
| aria2 / transmission | 单独服务 | 按需启 |

要启用,需要在 NAS UI 里开启对应下载服务,socket 才会被创建。

### 6.12 网盘 `/znetdisk/*` + `/zonedrive/*`

**重大**:32 个 `/znetdisk/*` 端点全部摸到(2026-07-12,从 pcweb JS bundle `home/static/js/async/10064.*.js` 提取)。后端是 PHP(`/zspace/applications/services/znetdisk/index.php`)+ Go RPC(`netdiskv2_server` 监听 :8026,二进制路径 `/var/appstore/pkg/cloudBackUp/znetdiskv2/`)。

**支持的网盘**:百度网盘(主集成,client_id=`GTXdyMi3Q0enYhpCfiaHscBRnY9ST0t6`)+ OneDrive(已配过,`/zspace/zsrp/sqlite/<user>/onedrive.db`)。

**OAuth 登录流程(百度,redirect_uri=oob 模式)**:
1. 调 `/znetdisk/auth/check` 拿 OAuth URL(返回 `https://openapi.baidu.com/oauth/2.0/authorize?client_id=...&scope=basic,netdisk`)
2. 用户在浏览器打开 URL,百度登录并授权 → 显示授权码(code)
3. 调 `/znetdisk/auth/token {app:"baidu", code:"..."}` 完成 token 交换
4. `is_login=true` 后所有端点可用

未登录时所有端点返回 `code="N001013"` + 空 data。

#### 6.12.1 已破端点清单(全部 POST,application/json)

⚠️ **MCP tool 已封装**(2026-07-13,commit `168fcd7`),tool 名 = `znetdisk_<group>_<action>`,如 `znetdisk_auth_check` / `znetdisk_share_transfer`。详见 `mcp_server/tools/znetdisk.py`。

**Auth(4)** ✅ 字段已实测
| 端点 | 用途 | 实测返回 |
|------|------|---------|
| `/znetdisk/auth/check` | 检查登录态 + 拿 OAuth URL | `{code:"200", data:{is_login:bool, url:string}}` ✅ |
| `/znetdisk/auth/token` | OAuth code → token 完成登录 | body `{app:"baidu"\|"onedrive", code:"..."}` |
| `/znetdisk/auth/userinfo` | 已登录网盘账号信息 | N001013 未登录 |
| `/znetdisk/auth/logout` | 退出网盘 | |

**File 操作(4)— 云盘文件管理**
| 端点 | 用途 | 关键 body |
|------|------|----------|
| `/znetdisk/file/list` | 列云盘文件 | `{dir, page, num}`(待抓) |
| `/znetdisk/file/download` | 添加下载任务(云盘 → NAS) | `{file_path[], save_path}`(待抓) |
| `/znetdisk/file/upload` | 上传(NAS → 云盘) | `{file_path[], save_path}` |
| `/znetdisk/file/newdir` | 云盘新建目录 | `{dir}` |

**Task(2)— 任务管理**
| 端点 | 用途 |
|------|------|
| `/znetdisk/task/list` | 列下载/上传任务 |
| `/znetdisk/task/action` | 任务操作(start/stop/delete) |

**Sync(6)— NAS ↔ 云盘双向同步**
| 端点 | 用途 |
|------|------|
| `/znetdisk/sync/list` | 列同步任务 |
| `/znetdisk/sync/add` | 创建同步任务(local_dir + remote_dir) |
| `/znetdisk/sync/home` | 同步主页(统计/汇总) |
| `/znetdisk/sync/open` | 启用同步任务 |
| `/znetdisk/sync/close` | 暂停同步任务 |
| `/znetdisk/sync/delete` | 删除同步任务 |

**AutoBackup(7)— 自动备份(手机/电脑 → NAS → 云盘)**
| 端点 | 用途 |
|------|------|
| `/znetdisk/autobackup/add` | 添加自动备份任务 |
| `/znetdisk/autobackup/info` | 任务详情 |
| `/znetdisk/autobackup/start` | 启动 |
| `/znetdisk/autobackup/stop` | 暂停 |
| `/znetdisk/autobackup/delete` | 删除 |
| `/znetdisk/autobackup/faillist` | 失败文件列表 |
| `/znetdisk/autobackup/clear_fail_files` | 清失败记录 |

**Share(4)— 分享链接转存(用户场景:别人发的百度网盘链接 → 我的 NAS)**
| 端点 | 用途 |
|------|------|
| `/znetdisk/share/verify` | 验证分享链接(提取码) |
| `/znetdisk/share/filelist` | 分享里的文件列表 |
| `/znetdisk/share/transfer` | 转存到我的网盘 |
| `/znetdisk/share/transfer_result` | 转存结果 |

**Fail / Membership / Order(7)**
| 端点 | 用途 |
|------|------|
| `/znetdisk/fail/list` | 全局失败列表 |
| `/znetdisk/membership/active` | 会员激活 |
| `/znetdisk/order/check_free_vip` | 检查免费 VIP |
| `/znetdisk/order/direct_charge` | 直接充值 |
| `/znetdisk/order/get_cashier` | 收银台 |

#### 6.12.2 其他网盘后端(活跃,路径未破)

| 端点前缀 | 后端 | 状态 |
|---------|------|------|
| `/znetdisk1/` | 127.0.0.1:8026(Go `netdiskv2_server`)| 跟 `/znetdisk/` 共享 PHP 入口? |
| `/netdisk/` `/netdisk/ws` | 127.0.0.1:5300 | WebSocket,可能用于实时进度 |
| `/zonedrive/` | `zonedrive.socket` | 网盘**统一抽象层**,子路径全 404,需更多抓包 |

#### 6.12.3 配置文件(已摸到)

- **`/var/appstore/pkg/cloudBackUp/znetdiskv2/conf/config.yml`** — Go 后端配置(server.port=8000,DB MySQL `nas` 库,Redis cluster)
- **`/var/appstore/pkg/cloudBackUp/znetdiskv2/conf/error.yml`** — 错误码:
  - `11`:百度网盘账号已被其他极空间账号绑定
  - `13/15`:需要 NAS 会员权限
  - `19/20`:文件数限制(5/100)
- **`/var/appstore/pkg/cloudBackUp/znetdiskv2/conf/conf.go`** — 文件系统挂载点常量:
  - `/tmp/zfuse`、`/tmp/zfsv2`、`/tmp/zfsv3`(版本演进)
  - `/tmp/zfsv2/share`、`/tmp/zfsv2/share_for`(共享)
  - `/zspace/extdev`(USB 外置)

#### 6.12.4 关键 gap

- **OAuth 流程未跑通** — 需要用户在 NAS pcweb UI(或 MCP 触发 + 浏览器)实际登录百度网盘账号,拿到 code 后才能调其他端点
- **写端点 body 未抓全** — `share/verify`、`sync/add`、`autobackup/add`、`task/action` 的字段名待从 JS 深挖或登录后实测
- **`/zonedrive/*` 路径未破** — 这是统一抽象层,可能覆盖 OneDrive/夸克/阿里云盘等

### 6.13 其他活跃后端(从 unix socket 列表确认)

NAS 上 `/dev/shm/*.socket` 显示以下服务在跑(全部可通过 openresty 反代):

| 服务 | socket | 推测端点前缀 |
|------|--------|--------------|
| appstore | appstore.socket | `/appstore/*` |
| filerescue | filerescue.socket | 文件救援 |
| filesearchserver | filesearchserver.socket | `/file_search/*` |
| fss | fss.socket | 文件系统服务? |
| ledsserver | ledsserver.socket | `/local/led/*` |
| mailbackup | mailbackup.socket | 邮件备份(`/var/appstore/pkg/mailbackup/`) |
| mediaconverter | mediaconverter.socket | `/zvideo/converter/*` |
| netdiskv2server | netdiskv2server.socket | `/znetdisk/*` |
| storagepool | storagepool.socket | `/storagepool/*` |
| transcode | transcode.socket | `/transcode/*` |
| upgrader | upgrader.socket | `/upgrader/*` |
| wxrobot | wxrobot.socket | 微信机器人(备份?) |
| zalbumv2 | zalbumv2.socket | `/v2/album/*` |
| zbakcenter / zbakv2 | 同名 socket | 备份中心 |
| zbasic | zbasic.socket | 基础服务 |
| zdocker | zdocker.socket | Docker 管理 |
| zdrive | zdrive.socket | 极空间云盘 |
| zfamily | zfamily.socket | 家庭相册分享 |
| zfilev2 | zfilev2.socket | `/v2/file/*` |
| zfirewall | zfirewall.socket | 防火墙 |
| zflash | zflash.socket | 闪存? |
| zmusicv2 | zmusicv2.socket | `/zmusic/*` |
| zonedrive | zonedrive.socket | 网盘统一 |
| zsanmanager | zsanmanager.socket | SAN 管理 |

### 6.14 用户启用应用后的扩展服务(2026-06-25 启用)

> 用户在 NAS UI 启用了:下载 / 有声读物 / 极阅读 / 极漫画 / 邮件备份 / FTP 备份 + 验证 webdav/smb 等。新增 socket:`downloader / zaudio / zreader / zcomic / mailbackup / ftprsync(8013 端口)` + 进程 `qbittorrent-nox / aria2c`。

#### 6.14.1 下载 `/downloader/*`(实测可用)

| 端点 | 用途 | 关键 body |
|------|------|----------|
| `/downloader/list` | 全部下载任务 | `{}` 返回 `{list[{id,type,downloadDir,totalSize,completeSize,isFinished,status,rateDownload,rateUpload,uri,...}], total, totalRateDownload, seedingTaskCount, ...}` |
| `/downloader/share/add` | 添加分享任务 | 需参数 |

实测:本机有 1 个 BT 任务(系统镜像包,3.27GB/2.93GB,~90%)。

**实际下载引擎**(独立进程,有自己的 web 端口):
- `qbittorrent-nox` 监听 `:51413`(BT 协议端口)+ webui 经 `58082_qbittorrent.conf` 反代
- `aria2c` 进程在跑(配置 `/zspace/zsrp/downloader/aria2/conf/aria2c.conf`)
- `xunlei` 在 `/xunlei/`(127.0.0.1:5052)

#### 6.14.2 SMB / WebDAV / FTP / DLNA 状态 `/api/fileshare_service/*`

**全部 200 直通(POST 空 body,GET 返回空字典!)**:

| 端点 | 实测数据 |
|------|---------|
| `/api/fileshare_service/samba/status` | `{audit, guest, host_name, ios_support, mc, ntlm, size, status, tiny_file}` —— 本机 status=true,host=Z4Pro-NY4H |
| `/api/fileshare_service/webdav/status` | `{http_port, https_port, status}` —— 本机 status=true,http_port=5005,https_port=5006 |
| `/api/fileshare_service/ftp/status` | `{exits, guest, passive, passive_ip, passive_port_start/end, port, status}` —— 本机 status=false(未启),port=21,passive 40000-45000 |
| `/api/fileshare_service/dlna/status` | `{is_share, status}` —— 本机 status=false |
| `/api/fileshare_service/tm/status` | Time Machine 状态(参数错) |

⚠️ **GET 返回空 dict**,**POST `{}` 才返真数据**。

**配置类端点报"服务端参数错误"**(需要具体 body):`samba/config`, `samba/mapping/list`, `webdav/config`, `ftp/config/guest`, `dlna/dir` 等。

#### 6.14.3 启用了但端点未明的服务

| 服务 | 状态 |
|------|------|
| `zaudio` 有声读物 | socket 活,但常见路径(`/api/v2/*`, `/book/list`)全 404,需 JS 深挖 |
| `zreader` 极阅读 | 同上,java 实现(jar 包 `/var/appstore/pkg/bookLib/zreader`),独立端口 8029 |
| `zcomic` 极漫画 | 同上,独立 socket |
| `mailbackup` 邮件备份 | socket 活,进程 mailmanager 监听 9998,需 NAS UI 抓包确定 API |
| `ftprsync` FTP 备份 | 进程在 8013,常见路径返回 HTML 目录列表样式 |

#### 6.14.4 AI Lab(发现新大陆!)

`zcomic.conf` 里发现 NAS 有 **AI Lab 功能**(本地 LLM):

| 路径 | 后端 | 用途 |
|------|------|------|
| `/AiLab/*` | `deepseekmgr.socket` | DeepSeek 模型管理 |
| `/AiLabApi/Llamas/*` | `127.0.0.1:45463` | LLaMA 模型服务 |
| `/AppCenter/*` | `onlyofficemgr.socket` | OnlyOffice 应用中心 |

⚠️ 实测全部 502(后端没起,需要单独启用 AI Lab 功能)。这是一个**未被官方宣传的隐藏 AI 能力**,如果启用,理论上可以本地跑 LLM 对话。

---

## 7. 文件搜索 `/file_search/*`

| 端点 | 用途 |
|------|------|
| `/file_search/file_search` | 按文件名搜 |
| `/file_search/office_search` | office 文档全文搜 |
| `/file_search/slow_file_search/{start,stop,get_result}` | 后台慢搜(异步) |
| `/file_search/get_setting` `/save_setting` | 索引设置 |
| `/file_search/reindex` `/check_reindex` | 重建索引 |
| `/file_search/skip_dir/{list,add,delete}` | 跳过目录 |
| `/file_search/search_log/{get,delete}` | 搜索日志 |

**实测**:backend 自身要 token,所以即便走 `/local/file_search/...` 也需要登录态。

---

## 8. 共享服务 `/api/fileshare_service/*`

| 端点 | 用途 |
|------|------|
| `/api/fileshare_service/samba/status\|config\|enable` | Samba |
| `/api/fileshare_service/webdav/status\|config\|enable` | WebDAV |
| `/api/fileshare_service/ftp/status\|config/{guest}\|enable` | FTP |
| `/api/fileshare_service/dlna/status\|dir\|enable` | DLNA |
| `/api/fileshare_service/tm/status\|config\|delete` | Time Machine 备份 |
| `/api/fileshare_service/samba/mapping/list\|rename\|del` | Samba 用户映射 |
| `/api/fileshare_service/ssl/reload` | 证书重载 |

---

## 9. 设备/账号 `/auth/*`(已登录后)

| 端点 | 用途 |
|------|------|
| `/auth/token` | 校验当前 token(openresty 内部用) |
| `/auth/master` | 是否有 master 账号 |
| `/auth/ismaster` | 用户名是否为 master |
| `/auth/pwdcheck` | 仅校验密码不签发 token |
| `/auth/tmptoken` | 主账号操作子账号的临时 token |
| `/auth/loginlist` | 该账号已登记的设备列表 |

---

## 10. 状态码速查

| code | 含义 |
|------|------|
| `200` | 成功(注意是字符串) |
| `N001200` | 账号格式不对或已存在 |
| `N001208` | token 已失效 |
| `N001212` | 参数有误 |
| `N001411` | 无权限进行此操作(常见:path 越权或 device 不匹配) |
| `N001414` | 新设备登陆,需进行安全验证(SMS) |
| `N001603` | 保险箱未打开 |
| `N302001` | 存储池已升级到 v3(用了旧 `/storagepool/` 端点) |

---

## 11. 已知坑

1. **`/system/*` 外部一律 404**:openresty `location /local/` 只允许 `127.0.0.1`。要拿系统数据用 `/zstatus` 或 SSH 隧道。
2. **`server_pubkey` ≠ `pubkey`**:前者 1024-bit(注册流程用),后者 2048-bit(登录用)。
3. **device_id 严格 32 字符**:`strlen != 32` 直接拒绝。
4. **path 必须含 `/my/` 子目录**:`/sata14/` 和 `/sata14/my/` 都报 N001411。
5. **POST 默认 form-urlencoded**:不是 JSON,pcweb 默认行为,要照抄。
6. **所有请求都带公共 query params**:`plat` + `version` + `device_id` + `device` + `_l`。
7. **Docker API 明文暴露在 LAN**:`192.168.0.135:2375`,任何同网段客户端都能拿 root(安全隐患,跟 MCP 无关但建议关)。

---

## 12. 模拟客户端最小代码片段

```python
import base64
import httpx
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

PUBKEY = load_pem_public_key(NAS_PUBKEY_PEM)  # 见 1.1
DEVICE_ID = "<your_device_id_32_hex>"  # 复用已登记的 device_id

def enc(s: str) -> str:
    return base64.b64encode(PUBKEY.encrypt(s.encode(), padding.PKCS1v15())).decode()

# 1) 登录
c = httpx.Client(timeout=10)
r = c.post("http://192.168.0.135:5055/auth/login", data={
    "username": enc("<your_phone_number>"),
    "password": enc(PASSWORD),
    "plat": "web", "device": "linux", "device_id": DEVICE_ID,
})
token = r.json()["data"]["token"]
c.cookies.update({"token": token, "username": "<your_phone_number>",
                  "device_id": DEVICE_ID, "device": "linux", "plat": "web"})

# 2) 公共 query 追加(每个请求都加)
def url(path):
    return f"http://192.168.0.135:5055{path}?plat=web&version=2.3.2026062201&device_id={DEVICE_ID}&device=linux&_l=zh-CN"

# 3) 列文件
r = c.post(url("/v2/file/list"), data={
    "folderId": 0, "path": "/sata14/my/data/",
    "start": 0, "num": 100, "sortby": "name", "order": "asc", "show_hidden": 0,
})
print(r.json())
```
