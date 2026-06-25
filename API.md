# ZSpace Z4Pro NAS API 速查

> 适用型号:Z4Pro(固件 `V1.0.0430455` / `Z043_SERVICE`)
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

**公钥**(`NAS_PUB_KEY_FILE = /zspace/system/private/pubkey`,base64 解开后是 2048-bit PEM):
```
-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtrDHnaRmRaMAhZC2CmRV
CPO3ekJRo5ELX3Jjtr9P8MoWHSQbsAE5G+VTkKWhTyMQQMR0erKabn82fOZgyOO4
F+CVRSJH0TRD854IeQyFD2iZg2W2J/BzYNYC8EmBjlRhs8oS5LBc0WUN7bP4et0s
Z2LGSXbt6TetSndeV9LP8+zaKka+xvV/9aohg5rc5Ha5ka7BfTliBOyzLPR+UTKe
mx9ysWrXedlYGUjXkDRyp4xfj98bOx44EmswJh+YHYNSINyCZ4nMsat98aWOPEDl
jsflEvNt6vXFDqrziOjAPW0S/wvyvrFCZxlb+IxJMrtNH7M61spGfobE8sjNU+MC
wwIDAQAB
-----END PUBLIC KEY-----
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
    "username": "15068832031",
    "nickname": "cherry",
    "is_master": 1,
    "actived": 1,
    "token": "108MSQlMTc1M...",
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
sqlite3 /zspace/system/db/user.db "SELECT id FROM user WHERE username='15068832031'"
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

| 端点 | 用途 | 备注 |
|------|------|------|
| `/v2/file/info` | 单文件元数据 | 需 `path` |
| `/v2/file/copy` | 复制 | |
| `/v2/file/move` | 移动 | |
| `/v2/file/modify` | 重命名/属性 | |
| `/v2/file/download` | 下载 | |
| `/v2/file/hash` | 计算哈希 | |
| `/v2/file/latest/list` | 最近访问列表 | 空 body 可用 |
| `/v2/file/categories` | 按类型统计 | 空 body 可用,返回 `{categories:{...}}` |
| `/v2/file/dwlist` | 下载任务列表 | 空 body 可用 |
| `/v2/file/empty_dir` | 找空目录 | |
| `/v2/file/decompress/*` | 解压 | 带密码的 `setpwd` |
| `/v2/compression/*` | 压缩 | |
| `/v2/file/notepad/*` | 笔记 | 需要"保险箱"开启 |
| `/v2/file/decrypt/download` | 加密文件下载 | |

---

## 5. 极影视(`/zvideo/*`)

服务端口实际在 `:8111`(zvideoapi PHP),通过 openresty 5055 反代。

### 5.1 分类(已验证)

| 端点 | 用途 | 关键 body |
|------|------|----------|
| `/zvideo/classification/list` | 列出所有分类 | `{}` |
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

---

## 6. 文件搜索 `/file_search/*`

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

## 7. 共享服务 `/api/fileshare_service/*`

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

## 8. 设备/账号 `/auth/*`(已登录后)

| 端点 | 用途 |
|------|------|
| `/auth/token` | 校验当前 token(openresty 内部用) |
| `/auth/master` | 是否有 master 账号 |
| `/auth/ismaster` | 用户名是否为 master |
| `/auth/pwdcheck` | 仅校验密码不签发 token |
| `/auth/tmptoken` | 主账号操作子账号的临时 token |
| `/auth/loginlist` | 该账号已登记的设备列表 |

---

## 9. 状态码速查

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

## 10. 已知坑

1. **`/system/*` 外部一律 404**:openresty `location /local/` 只允许 `127.0.0.1`。要拿系统数据用 `/zstatus` 或 SSH 隧道。
2. **`server_pubkey` ≠ `pubkey`**:前者 1024-bit(注册流程用),后者 2048-bit(登录用)。
3. **device_id 严格 32 字符**:`strlen != 32` 直接拒绝。
4. **path 必须含 `/my/` 子目录**:`/sata14/` 和 `/sata14/my/` 都报 N001411。
5. **POST 默认 form-urlencoded**:不是 JSON,pcweb 默认行为,要照抄。
6. **所有请求都带公共 query params**:`plat` + `version` + `device_id` + `device` + `_l`。
7. **Docker API 明文暴露在 LAN**:`192.168.0.135:2375`,任何同网段客户端都能拿 root(安全隐患,跟 MCP 无关但建议关)。

---

## 11. 模拟客户端最小代码片段

```python
import base64
import httpx
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

PUBKEY = load_pem_public_key(NAS_PUBKEY_PEM)  # 见 1.1
DEVICE_ID = "a6b4bd9ea4839ab4aea6f22b558bf0b2"  # 复用已登记的 device_id

def enc(s: str) -> str:
    return base64.b64encode(PUBKEY.encrypt(s.encode(), padding.PKCS1v15())).decode()

# 1) 登录
c = httpx.Client(timeout=10)
r = c.post("http://192.168.0.135:5055/auth/login", data={
    "username": enc("15068832031"),
    "password": enc(PASSWORD),
    "plat": "web", "device": "linux", "device_id": DEVICE_ID,
})
token = r.json()["data"]["token"]
c.cookies.update({"token": token, "username": "15068832031",
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
