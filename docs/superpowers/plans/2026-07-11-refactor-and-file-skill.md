# Refactor + File Diagnostic Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `app.py`(2045 行)和 `mcp_server.py`(1285 行)按域拆成分包,抽公共 NAS 加密/常量层;然后写一个只读文件诊断 skill(找重复文件 + 孤儿文件),延续 `media-organizer` 模式。

**Architecture:**
- **Phase 1(拆分)**:新增顶层包 `nas/`(纯函数 + 常量,4 处共用)、`mcp_server/`(原 mcp_server.py 按域拆)、`app/`(原 app.py 按域拆)。保留 `mcp_server.py` 和 `app.py` 作薄入口 shim,外部 `mcp.json` 和 `start.sh` 零改动。
- **Phase 2(skill)**:新增 `.claude/skills/file-organizer/`,Python 脚本扫 NAS 全盘输出重复/孤儿文件清单(JSON + 文本),LLM 看完报告用户决定后续。**只读,不动 NAS。**

**Tech Stack:** Python 3.10+,FastAPI,FastMCP,httpx,cryptography,Jinja2。skill 脚本走同步 httpx(沿用 media-organizer 模式)。

## Global Constraints

- **入口路径不破**:`mcp_server.py` 和 `app.py` 必须仍然能被 `python mcp_server.py` / `uvicorn app:app` 启动。外部 `mcp.json` 的 `args: ["$ROOT/mcp_server.py"]` 不改。
- **skill import 不破**:`.claude/skills/*/lib/nas_client.py` 里的 `from mcp_server import NasClient` 必须继续 work(靠 `mcp_server/__init__.py` 重导出)。
- **环境变量名不改**:`NAS_HOST/NAS_USER/NAS_PASSWORD/NAS_DEVICE_ID/KEY_SSH/NAS_SSH_PORT/ZENITH_COOKIE/SESSION_SECRET` 全部保留。
- **NAS 端点路径不改**:所有 `/v2/file/*`、`/zvideo/*`、`/v2/file/notepad/*` 等保持原样,只是搬位置。
- **验证手段**:此 PoC 无单测。每个 task 用冒烟测试验证:(a) `python -c "import ..."` 不报错;(b) MCP 启动看到 `58 tools registered`;(c) Dashboard 登录跑通;(d) skill 脚本 `--help` + 实跑输出 JSON。
- **每 task 一个 commit**,commit message 用 `refactor:` / `feat:` 前缀。
- **DRY**:抽公共层的目的是消除 4 处 RSA 公钥/加密函数的重复。

---

## File Structure(最终形态)

```
zspace-mcp-poc/
├── nas/                          # 新增:NAS 协议层(纯函数 + 常量)
│   ├── __init__.py               # 重导出 NasClient / encrypt / pubkey 等
│   ├── auth.py                   # RSA 公钥 PEM + encrypt() + resolve_device_id()
│   ├── proto.py                  # NAS_BASE, _common_query(), _append_common_query()
│   └── client.py                 # NasClient(async,从 mcp_server.py:88-259 搬过来)
│
├── mcp_server/                   # 新增:MCP server 按域拆
│   ├── __init__.py               # from .client import NasClient; from .main import mcp  (skill 兼容)
│   ├── main.py                   # FastMCP 实例 + 启动逻辑(原 487-498, 1263-1285)
│   ├── zenith.py                 # ZenithSession(原 261-332)
│   ├── perf.py                   # _parse_perf / _ssh_perf / _parse_zstatus(原 334-485)
│   ├── rag_hook.py               # _rag_hook + RAG 占位(原 50, 500-517)
│   └── tools/
│       ├── __init__.py           # 空(只是包标记)
│       ├── files.py              # 文件类 tool(原 519-562, 1007-1105)
│       ├── storage.py            # 池/硬件/SMART/监控(原 563-609)
│       ├── zvideo.py             # 影视 6读+2写(原 610-696, 1108-1166)
│       ├── media.py              # 音乐/相册(原 697-717)
│       ├── shares.py             # 下载/分享/共享服务(原 718-763)
│       ├── notebook.py           # 17 个 notebook tool(原 785-1005)
│       ├── proxy.py              # proxy_* 4 个(原 1174-1260)
│       └── rag.py                # 可选,try import rag.mcp_tools(原 RAG 占位实装)
│
├── mcp_server.py                 # 改成 1 行 shim:from mcp_server.main import main; main()
│
├── app/                          # 新增:FastAPI app 按域拆
│   ├── __init__.py               # from .main import app  (uvicorn app:app 兼容)
│   ├── main.py                   # create_app() + middleware + 注册所有 router
│   ├── deps.py                   # _require_login / _common_ctx / session helper
│   ├── nas_helpers.py             # _nas_get / _nas_post / _append_common_query(基于 session cookies)
│   ├── shortcut_client.py        # _get_shortcut_nas_client + _reset_shortcut_nas_client + _title_eq
│   ├── cocoa.py                  # _cocoa_html_to_clean(原 234-396)
│   ├── perf.py                   # _ssh_perf_snapshot + _parse_perf + _get_perf_cached(原 69-412)
│   ├── zstatus.py                # parse_zstatus + fmt_bytes + datetime_local + build_breadcrumb
│   ├── routes/
│   │   ├── __init__.py           # 空
│   │   ├── auth.py               # /, /login, /logout(原 414-481, 764-769)
│   │   ├── dashboard.py          # /dashboard/{overview,storage,zvideo,notebook}(原 525-793, 1520-1571)
│   │   ├── files.py              # /action/{mkdir,rename,move,copy,remove,info,...}(原 1327-1490)
│   │   ├── notebook.py           # /action/notebook-*(24 个,原 1572-2045)
│   │   ├── zvideo.py             # /action/{add-classification,link-folder}(原 1344-1399)
│   │   ├── shortcut.py           # /shortcut/notepad + /n PWA(原 882-1301)
│   │   └── proxy.py              # /_proxy GET/POST + /healthz + /api/perf(原 764-786, 1302-1326)
│   └── templates/                # 不动(templates/ 移到 app/templates/,或 Jinja2 指向上级)
│
├── app.py                        # 改成 1 行 shim:from app.main import app
│
├── templates/                    # 保留原位置(app/main.py 配 Jinja2Templates(directory="templates/"))
├── start.sh                      # 不改(入口路径不变)
├── .env.example                  # 不改
├── requirements.txt              # 不改
├── README.md                     # Phase 1 Task 4 更新导入路径说明
├── API.md                        # 不改(端点表不变)
├── MCP.md                        # Phase 1 Task 4 更新导入路径说明
│
├── .claude/skills/
│   ├── label-manager/            # 不改(lib/nas_client.py 桥接仍 work)
│   ├── media-organizer/          # 不改
│   └── file-organizer/           # 新增:Phase 2
│       ├── SKILL.md
│       ├── README.md
│       ├── file_organizer.py     # 主脚本(扫描 + 报告)
│       └── lib/
│           └── nas_client.py     # 桥接层(同 media-organizer 模式)
```

---

## Phase 1:拆分重构

### Task 1:抽 `nas/` 公共包

**Files:**
- Create: `nas/__init__.py`, `nas/auth.py`, `nas/proto.py`
- Test: `python -c "from nas.auth import encrypt_field, resolve_device_id; from nas.proto import NAS_BASE, common_query"`

**Interfaces:**
- Produces:
  - `nas.auth.NAS_PUBKEY_PEM: bytes`(常量,从 app.py:25-33 / mcp_server.py:61-69 原样搬)
  - `nas.auth.encrypt_field(plain: str) -> str`(RSA-PKCS1v15 + base64)
  - `nas.auth.resolve_device_id() -> str`(读 env `NAS_DEVICE_ID`,默认 `<your_device_id_32_hex>`)
  - `nas.proto.NAS_BASE: str`(从 env 读,默认 `http://192.168.0.135:5055`)
  - `nas.proto.common_query(device_id: str) -> str`(原 mcp_server.py:80-85 的 `_common_query`,改成接 device_id 参数)
  - `nas.proto.append_common_query(url: str, device_id: str) -> str`(原 app.py:513 的 `_append_common_query`)

- [ ] **Step 1.1:建目录建文件**

```bash
mkdir -p nas
```

- [ ] **Step 1.2:写 `nas/auth.py`**

```python
"""NAS 登录加密层(RSA-PKCS1v15 + base64)。

4 处复用:app.py、mcp_server.py、.claude/skills/*/lib/。
"""
import base64
import os

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

NAS_PUBKEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtrDHnaRmRaMAhZC2CmRV
CPO3ekJRo5ELX3Jjtr9P8MoWHSQbsAE5G+VTkKWhTyMQQMR0erKabn82fOZgyOO4
F+CVRSJH0TRD854IeQyFD2iZg2W2J/BzYNYC8EmBjlRhs8oS5LBc0WUN7bP4et0s
Z2LGSXbt6TetSndeV9LP8+zaKka+xvV/9aohg5rc5Ha5ka7BfTliBOyzLPR+UTKe
mx9ysWrXedlYGUjXkDRyp4xfj98bOx44EmswJh+YHYNSINyCZ4nMsat98aWOPEDl
jsflEvNt6vXFDqrziOjAPW0S/wvyvrFCZxlb+IxJMrtNH7M61spGfobE8sjNU+MC
wwIDAQAB
-----END PUBLIC KEY-----"""

_PUBKEY = load_pem_public_key(NAS_PUBKEY_PEM)

NAS_DEVICE_ID_DEFAULT = "<your_device_id_32_hex>"


def encrypt_field(plain: str) -> str:
    """RSA-PKCS1v15 + base64. NAS /auth/login 要求."""
    cipher = _PUBKEY.encrypt(plain.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(cipher).decode("ascii")


def resolve_device_id() -> str:
    """优先 env NAS_DEVICE_ID,否则用代码默认值。始终 32 字符。"""
    did = os.environ.get("NAS_DEVICE_ID", "").strip()
    return did if (len(did) == 32) else NAS_DEVICE_ID_DEFAULT
```

- [ ] **Step 1.3:写 `nas/proto.py`**

```python
"""NAS HTTP 协议层:base URL + 公共 query 参数(axaxios 拦截器追加)。"""
import os

NAS_BASE = os.environ.get("NAS_BASE", "http://192.168.0.135:5055")


def common_query(device_id: str) -> str:
    """axios 拦截器给所有请求追加的公共参数。"""
    return (
        f"?plat=web&version=2.3.2026062201"
        f"&device_id={device_id}&device=linux&_l=zh-CN"
    )


def append_common_query(url: str, device_id: str) -> str:
    """给 NAS API URL 拼上公共参数(原 app.py:_append_common_query)。"""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{common_query(device_id).lstrip('?')}"
```

- [ ] **Step 1.4:写 `nas/__init__.py`**

```python
"""NAS 协议层公共包。"""
from .auth import (
    NAS_PUBKEY_PEM,
    NAS_DEVICE_ID_DEFAULT,
    encrypt_field,
    resolve_device_id,
)
from .proto import NAS_BASE, common_query, append_common_query

__all__ = [
    "NAS_PUBKEY_PEM",
    "NAS_DEVICE_ID_DEFAULT",
    "encrypt_field",
    "resolve_device_id",
    "NAS_BASE",
    "common_query",
    "append_common_query",
]
```

- [ ] **Step 1.5:验证 import**

```bash
python -c "from nas import encrypt_field, resolve_device_id, NAS_BASE, common_query, append_common_query; print('ok', NAS_BASE)"
```
Expected: `ok http://192.168.0.135:5055`

- [ ] **Step 1.6:Commit**

```bash
git add nas/
git commit -m "refactor: 抽 nas/ 公共包(RSA 公钥 + 加密 + URL 协议层)"
```

---

### Task 2:抽 `nas/client.py`,搬 NasClient 类

**Files:**
- Create: `nas/client.py`(从 `mcp_server.py:88-259` 搬 `NasClient` 类)
- Modify: `nas/__init__.py`(重导出 `NasClient`)
- Test: `python -c "from nas import NasClient; print(NasClient)"`

**Interfaces:**
- Produces: `nas.client.NasClient` 类(async,token 自动续期)
  - `__init__(self)` — 从 env 读 NAS_HOST/USER/PASSWORD/DEVICE_ID
  - `async def login(self) -> dict`
  - `async def get(self, path: str) -> dict`
  - `async def post(self, path: str, data: dict | None = None) -> dict`
  - 属性:`_profile: dict`(登录后填),`_cookies: dict`
- Consumes:`nas.auth.encrypt_field`、`nas.proto.NAS_BASE`、`nas.proto.common_query`

- [ ] **Step 2.1:写 `nas/client.py`**

把 `mcp_server.py:88-259` 的 `NasClient` 类**原样复制**到 `nas/client.py`,做这些改动:
- 删掉文件内的 `NAS_BASE` / `NAS_HOST` / `_encrypt` / `_common_query` 定义(已在 `nas.auth` / `nas.proto`)
- import 改成:`from .auth import encrypt_field, resolve_device_id` 和 `from .proto import NAS_BASE, common_query`
- 类内部原本调 `_encrypt(...)` 改成 `encrypt_field(...)`
- 类内部原本调 `_common_query()` 改成 `common_query(self._device_id)`,其中 `self._device_id = resolve_device_id()` 在 `__init__` 里设
- 其他逻辑(token 续期、`_maybe_relogin`、`_login_lock`、httpx.AsyncClient 池化)**原样保留**

- [ ] **Step 2.2:更新 `nas/__init__.py`**

在末尾追加:

```python
from .client import NasClient
__all__.append("NasClient")
```

- [ ] **Step 2.3:验证 import + 类签名**

```bash
python -c "
from nas import NasClient
import inspect
sig = inspect.signature(NasClient.__init__)
print('NasClient OK, methods:', [m for m in dir(NasClient) if not m.startswith('_') or m in ('_profile','_cookies')])
"
```
Expected: 列出 `get`, `post`, `login` 等方法。

- [ ] **Step 2.4:Commit**

```bash
git add nas/client.py nas/__init__.py
git commit -m "refactor: NasClient 类搬到 nas/client.py(从 mcp_server.py 抽出)"
```

---

### Task 3:拆 `mcp_server.py` → `mcp_server/` 包

**这是最大的一个 task**。建议拆成多个子步骤,每完成一组 tool 文件就跑一次冒烟。

**Files:**
- Create: `mcp_server/__init__.py`, `mcp_server/main.py`, `mcp_server/zenith.py`, `mcp_server/perf.py`, `mcp_server/rag_hook.py`
- Create: `mcp_server/tools/{__init__.py, files.py, storage.py, zvideo.py, media.py, shares.py, notebook.py, proxy.py, rag.py}`
- Modify: `mcp_server.py`(改成 shim)

**搬迁映射表**:

| 原文件:行号 | 内容 | 目标文件 |
|---|---|---|
| `mcp_server.py:27-48` | imports + logging 配置 | `mcp_server/main.py`(顶部) |
| `mcp_server.py:53-86` | env vars + pubkey + `_encrypt` + `_common_query` | **删掉**(已抽到 `nas/`) |
| `mcp_server.py:88-259` | `NasClient` | **删掉**(已抽到 `nas/client.py`) |
| `mcp_server.py:261-332` | `ZenithSession` | `mcp_server/zenith.py` |
| `mcp_server.py:334-485` | `_to_json`, `_parse_perf`, `_ssh_perf`, `_parse_zstatus` | `mcp_server/perf.py` |
| `mcp_server.py:487-498` | `mcp = FastMCP(...)` + RAG 占位 | `mcp_server/main.py` |
| `mcp_server.py:500-517` | `_rag_hook` | `mcp_server/rag_hook.py` |
| `mcp_server.py:519-562` | files 5 读(list_files, file_info, recent_files, file_categories, list_file_labels) | `mcp_server/tools/files.py` |
| `mcp_server.py:563-609` | storage 6 读 + system_status + perf_snapshot | `mcp_server/tools/storage.py` |
| `mcp_server.py:610-696` | zvideo 6 读 | `mcp_server/tools/zvideo.py` |
| `mcp_server.py:697-717` | music/album 3 读 | `mcp_server/tools/media.py` |
| `mcp_server.py:718-763` | downloads/shares/services 8 读 | `mcp_server/tools/shares.py` |
| `mcp_server.py:764-783` | whoami(单独) | `mcp_server/tools/storage.py`(监控类) |
| `mcp_server.py:785-1005` | notebook 17 tool | `mcp_server/tools/notebook.py` |
| `mcp_server.py:1007-1045` | label(save_file_label, delete_label) | `mcp_server/tools/files.py` |
| `mcp_server.py:1046-1106` | file 写(mkdir/rename/move/copy/remove) | `mcp_server/tools/files.py` |
| `mcp_server.py:1108-1166` | zvideo 写 2 个 | `mcp_server/tools/zvideo.py` |
| `mcp_server.py:1168-1260` | proxy_* 4 个 | `mcp_server/tools/proxy.py` |
| `mcp_server.py:1263-1285` | `_startup` + `main` | `mcp_server/main.py` |
| RAG 占位实装(`_HAS_RAG`, `_rag_tools`) | | `mcp_server/tools/rag.py`(try import 模式) |

- [ ] **Step 3.1:建包骨架**

```bash
mkdir -p mcp_server/tools
touch mcp_server/__init__.py mcp_server/tools/__init__.py
```

- [ ] **Step 3.2:写 `mcp_server/main.py`**

```python
"""MCP server 入口:FastMCP 实例 + 启动逻辑。"""
import asyncio
import logging
import sys

from mcp.server.fastmcp import FastMCP

from nas import NasClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("zspace-mcp")

mcp = FastMCP("zspace-nas")

# 全局实例(_startup 时初始化)
nas: NasClient = None  # type: ignore
zenith = None  # type: ignore


# 触发所有 @mcp.tool() 注册(import 即注册)
from mcp_server.tools import files, storage, zvideo, media, shares, notebook, proxy  # noqa: E402,F401
from mcp_server.rag_hook import _rag_hook  # noqa: E402,F401

# 可选 RAG tool(包未安装时静默跳过)
try:
    from mcp_server.tools import rag  # noqa: E402,F401
except ImportError as e:
    log.warning("RAG module not available, skipping 3 rag tools: %s", e)


async def _startup():
    global nas, zenith
    nas = NasClient()
    await nas.login()
    # ZenithSession 在 proxy tool 内部 lazy init(避免启动时 ZENITH_COOKIE 缺失报错)


def main():
    asyncio.run(_startup())
    log.info("MCP server 'zspace-nas' starting, %d tools registered", len(mcp._tool_manager._tools))
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3.3:写 `mcp_server/zenith.py`**

把 `mcp_server.py:261-332` 的 `ZenithSession` 类原样搬过来。类内引用的全局变量(`ZENITH_COOKIE`、`ZENITH_COOKIE_EXTRA` 等)保留,在模块顶部读 env:

```python
"""Zenith 云代理 session(走 zos 公网子域名访问 NAS LAN 端口)。"""
import os
# ... 把原 mcp_server.py:261-332 整段搬过来 ...
# ZENITH_COOKIE 等常量在模块顶部 os.environ.get 即可
```

- [ ] **Step 3.4:写 `mcp_server/perf.py`**

把 `mcp_server.py:334-485` 的 `_to_json`, `_parse_perf`, `_ssh_perf`, `_parse_zstatus` 搬过来。这些是纯函数,无依赖。

- [ ] **Step 3.5:写 `mcp_server/rag_hook.py`**

```python
"""RAG 写时增量钩子。

写 tool(mkdir/rename/move/copy/remove/notebook_*)调用 _rag_hook 通知 RAG 模块。
rag 包未安装时静默 no-op。
"""
import logging

log = logging.getLogger("zspace-mcp")

_HAS_RAG = False
_rag_tools = None


def _rag_hook(hook_name: str, *args) -> None:
    """写操作成功后通知 RAG 模块。无 RAG 时 no-op。"""
    if not _HAS_RAG or _rag_tools is None:
        return
    try:
        handler = getattr(_rag_tools, hook_name, None)
        if handler:
            handler(*args)
    except Exception as e:
        log.warning("RAG hook %s failed: %s", hook_name, e)
```

- [ ] **Step 3.6:写每个 tools/*.py**

每个文件结构相同:

```python
"""<域> tool 集合。"""
from mcp_server.main import mcp, nas
from mcp_server.perf import _to_json  # 如需
# ... 其他需要的 import ...


@mcp.tool()
async def tool_name(...):
    """..."""
    # 原实现
```

**关键模式**:`from mcp_server.main import mcp, nas` — 由于 Python 模块缓存,所有 tool 文件 import 同一个 `mcp` 实例,装饰器注册到同一个 FastMCP。

逐个搬(每个域搬完跑一次冒烟):
- `tools/files.py` — 搬 `mcp_server.py:519-562, 1007-1106`(共 12 个 tool)
- `tools/storage.py` — 搬 `mcp_server.py:563-609, 764-783`(8 个 tool,含 whoami)
- `tools/zvideo.py` — 搬 `mcp_server.py:610-696, 1108-1166`(8 个 tool)
- `tools/media.py` — 搬 `mcp_server.py:697-717`(3 个 tool)
- `tools/shares.py` — 搬 `mcp_server.py:718-763`(7 个 tool)
- `tools/notebook.py` — 搬 `mcp_server.py:785-1005`(17 个 tool)
- `tools/proxy.py` — 搬 `mcp_server.py:1168-1260`(4 个 tool)

**写工具内部的 `_rag_hook` 调用保留**(`from mcp_server.rag_hook import _rag_hook`)。

- [ ] **Step 3.7:写 `mcp_server/tools/rag.py`(可选模块)**

把原 `mcp_server.py` 里的 RAG 实装搬过来(如果项目里有 rag/ 包),或者保留 try import 模式:

```python
"""可选 RAG tool。rag/ 包未安装时本模块 import 失败,main.py 跳过注册。"""
try:
    from rag.mcp_tools import register_rag_tools
except ImportError as e:
    raise ImportError(f"rag package not available: {e}") from e

from mcp_server.main import mcp, nas
register_rag_tools(mcp, nas)
```

- [ ] **Step 3.8:写 `mcp_server/__init__.py`**

```python
"""MCP server 包。

兼容旧 import:`from mcp_server import NasClient` 仍 work(skill lib/ 用)。
"""
from nas import NasClient  # 重导出,skill 兼容
from mcp_server.main import mcp, main

__all__ = ["NasClient", "mcp", "main"]
```

- [ ] **Step 3.9:把 `mcp_server.py` 改成 shim**

完整替换 `mcp_server.py` 内容:

```python
"""薄入口 shim — 真正的实现已搬到 mcp_server/ 包。

保留这个文件是为了:
1. 外部 mcp.json 的 args: ["$ROOT/mcp_server.py"] 不需改
2. 旧的 `python mcp_server.py` 启动方式不变
"""
from mcp_server.main import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 3.10:冒烟测试**

```bash
# 1. import 检查
python -c "from mcp_server import NasClient, mcp, main; print('ok, tools:', len(mcp._tool_manager._tools))"

# 2. skill 兼容性检查
python -c "import sys; sys.path.insert(0, '.claude/skills/media-organizer/lib'); import nas_client; print('skill bridge ok:', nas_client.NasClient)"

# 3. 完整 MCP 握手(参考 README:222)
NAS_USER=xxx NAS_PASSWORD=xxx python -c "
import asyncio, json, sys
async def m():
    p = await asyncio.create_subprocess_exec(sys.executable, 'mcp_server.py', stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=4*1024*1024)
    async def send(m):
        p.stdin.write((json.dumps(m)+'\n').encode()); await p.stdin.drain()
    async def recv():
        return json.loads(await p.stdout.readline())
    await send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'t','version':'1'}}})
    r = await recv()
    print('handshake ok, server:', r.get('result',{}).get('serverInfo'))
    # 列 tool
    await send({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})
    r = await recv()
    print('tools count:', len(r.get('result',{}).get('tools',[])))
asyncio.run(m())
"
```
Expected:
- import OK
- skill bridge OK
- MCP 握手返回 `serverInfo: {'name':'zspace-nas',...}`
- tools count: 58(或 61 含 RAG,如果 rag/ 已装)

- [ ] **Step 3.11:Commit**

```bash
git add mcp_server/ mcp_server.py
git commit -m "refactor: 拆 mcp_server.py (1285行) → mcp_server/ 包 (按域分文件)

- nas/ 提供加密协议层和 NasClient
- mcp_server/main.py: FastMCP 实例 + 启动
- mcp_server/tools/*.py: 按域分(files/storage/zvideo/media/shares/notebook/proxy)
- mcp_server.py 改成 shim,外部 mcp.json 零改动
- skill lib/nas_client.py 桥接层继续 work(__init__.py 重导出 NasClient)"
```

---

### Task 4:拆 `app.py` → `app/` 包

**Files:**
- Create: `app/__init__.py`, `app/main.py`, `app/deps.py`, `app/nas_helpers.py`, `app/shortcut_client.py`, `app/cocoa.py`, `app/perf.py`, `app/zstatus.py`
- Create: `app/routes/{__init__.py, auth.py, dashboard.py, files.py, notebook.py, zvideo.py, shortcut.py, proxy.py}`
- Modify: `app.py`(改成 shim)

**搬迁映射表**:

| 原文件:行号 | 内容 | 目标文件 |
|---|---|---|
| `app.py:1-22` | imports + logging | `app/main.py` |
| `app.py:24-33` | `NAS_BASE`, `NAS_PUBKEY_PEM` | **删掉**(用 `nas.NAS_BASE`) |
| `app.py:38-60` | `NAS_DEVICE_ID_DEFAULT`, `SESSION_SECRET`, `_PUBKEY`, `encrypt_field`, `resolve_device_id` | **删掉**(用 `nas.*`) |
| `app.py:64-100` | SSH 凭据 + `_ssh_perf_snapshot` | `app/perf.py` |
| `app.py:104-233` | `_parse_perf` | `app/perf.py` |
| `app.py:234-396` | `_cocoa_html_to_clean` | `app/cocoa.py` |
| `app.py:397-412` | `_get_perf_cached` | `app/perf.py` |
| `app.py:414-481` | index, login_form, login_submit | `app/routes/auth.py` |
| `app.py:482-524` | `_nas_get`, `_nas_post`, `_append_common_query` | `app/nas_helpers.py` |
| `app.py:525-531` | dashboard_root, `_require_login` 雏形 | `app/routes/dashboard.py` + `app/deps.py` |
| `app.py:531-544` | `_require_login`, `_common_ctx` | `app/deps.py` |
| `app.py:545-682` | tab_overview, tab_storage, tab_zvideo | `app/routes/dashboard.py` |
| `app.py:683-693` | `build_breadcrumb` | `app/zstatus.py` |
| `app.py:694-737` | `parse_zstatus` | `app/zstatus.py` |
| `app.py:738-763` | `fmt_bytes`, `datetime_local` | `app/zstatus.py` |
| `app.py:764-786` | logout, healthz, api_perf | `app/routes/{auth,proxy}.py` |
| `app.py:788-865` | shortcut client + `_title_eq` | `app/shortcut_client.py` |
| `app.py:882-1156` | POST /shortcut/notepad | `app/routes/shortcut.py` |
| `app.py:1157-1301` | GET /n PWA | `app/routes/shortcut.py` |
| `app.py:1302-1326` | /_proxy GET/POST | `app/routes/proxy.py` |
| `app.py:1327-1490` | /action/{mkdir,rename,move,copy,remove,info,add-classification,link-folder} | `app/routes/{files,zvideo}.py` |
| `app.py:1491-1519` | `_safe_html` | `app/notebook_helpers.py` 或 `app/routes/notebook.py` |
| `app.py:1520-1571` | tab_notebook | `app/routes/dashboard.py` |
| `app.py:1572-2045` | 24 个 /action/notebook-* | `app/routes/notebook.py` |

- [ ] **Step 4.1:建包骨架**

```bash
mkdir -p app/routes
touch app/__init__.py app/routes/__init__.py
```

- [ ] **Step 4.2:写 `app/main.py`**

```python
"""FastAPI app 入口:create_app + middleware + 注册 router。"""
import logging
import os

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.routes import auth, dashboard, files, notebook, zvideo, shortcut, proxy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("zspace-poc")

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if not SESSION_SECRET:
    import secrets
    SESSION_SECRET = secrets.token_hex(32)
    log.warning("SESSION_SECRET 未设置,已生成临时随机密钥(重启后会话失效)")


def create_app() -> FastAPI:
    app = FastAPI(title="ZSpace NAS PoC")
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

    # Jinja2 templates 指向项目根的 templates/
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory="templates/")
    app.state.templates = templates

    # 注册 router
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(files.router)
    app.include_router(notebook.router)
    app.include_router(zvideo.router)
    app.include_router(shortcut.router)
    app.include_router(proxy.router)

    return app


app = create_app()
```

- [ ] **Step 4.3:写 `app/deps.py`**

```python
"""FastAPI 依赖:登录态校验 + 共享模板上下文。"""
from typing import Optional
from fastapi import Request
from fastapi.templating import Jinja2Templates


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def require_login(request: Request) -> dict:
    """检查 session 是否登录,返回基础上下文。未登录跳转 /login。"""
    from fastapi.responses import RedirectResponse
    if not request.session.get("logged_in"):
        return RedirectResponse(url="/login", status_code=303)
    return _common_ctx(request)


def _common_ctx(request: Request) -> dict:
    """所有 dashboard 模板共用的上下文。"""
    return {
        "username": request.session.get("username", ""),
        "user_id": request.session.get("user_id", ""),
        "device_id": request.session.get("device_id", ""),
    }
```

(实际搬迁时把 `app.py:531-544` 的逻辑原样搬过来,这里只是骨架。)

- [ ] **Step 4.4:写 `app/nas_helpers.py`**

把 `app.py:482-524` 的 `_nas_get`、`_nas_post` 搬过来。**保留原签名**(接 `httpx.AsyncClient` + session cookies)。

```python
"""NAS HTTP helpers(基于 session cookies,用于 Dashboard 路由)。"""
from typing import Any, Dict
import httpx
from nas.proto import append_common_query
from nas.auth import resolve_device_id


async def nas_get(client: httpx.AsyncClient, path: str) -> Dict[str, Any]:
    # ... 原 _nas_get 实现 ...
```

- [ ] **Step 4.5:写 `app/shortcut_client.py`**

把 `app.py:788-865` 的 `_get_shortcut_nas_client`、`_reset_shortcut_nas_client`、`_title_eq` 搬过来。这是个独立的服务账户 client(用 env `NAS_USER`/`NAS_PASSWORD` 登录,不依赖 web session)。

- [ ] **Step 4.6:写 `app/cocoa.py`**

把 `app.py:234-396` 的 `_cocoa_html_to_clean` 原样搬。

- [ ] **Step 4.7:写 `app/perf.py`**

把 `app.py:64-100, 104-233, 397-412` 的 SSH 凭据、`_ssh_perf_snapshot`、`_parse_perf`、`_get_perf_cached` 搬过来。

- [ ] **Step 4.8:写 `app/zstatus.py`**

把 `app.py:683-763` 的 `build_breadcrumb`、`parse_zstatus`、`fmt_bytes`、`datetime_local` 搬过来。

- [ ] **Step 4.9:写每个 routes/*.py**

每个文件用 `APIRouter()` 模式:

```python
"""<域> 路由。"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from app.deps import require_login, get_templates
from app.nas_helpers import nas_get, nas_post

router = APIRouter()


@router.get("/dashboard/xxx", response_class=HTMLResponse)
async def tab_xxx(request: Request):
    # 原实现
```

逐个搬:
- `routes/auth.py` — `app.py:414-481, 764-769`(index/login_form/login_submit/logout)
- `routes/dashboard.py` — `app.py:525-682, 1520-1571`(**4 个 GET tab**:overview/storage/zvideo/notebook,含 tab_notebook)
- `routes/files.py` — `app.py:1327-1490`(/action/{mkdir,rename,move,copy,remove,info})
- `routes/zvideo.py` — `app.py:1344-1399`(add-classification, link-folder)
- `routes/notebook.py` — `app.py:1491-1519, 1572-2045`(**24 个 /action/notebook-* POST/GET 端点** + `_safe_html` helper 跟着走)
- `routes/shortcut.py` — `app.py:882-1301`(POST /shortcut/notepad + GET /n PWA)
- `routes/proxy.py` — `app.py:764-786, 1302-1326`(/healthz, /api/perf, /_proxy GET/POST;logout 在 auth.py)

**路由归属原则**:GET 渲染 tab 的归 `dashboard.py`(overview/storage/zvideo/notebook 4 个 tab 集中);`/action/*` 端点按域分(files/zvideo/notebook);shortcut/PWA 单独;proxy 调试端点单独。

- [ ] **Step 4.10:写 `app/__init__.py`**

```python
"""FastAPI app 包。

兼容旧 import:`uvicorn app:app` 仍 work。
"""
from app.main import app

__all__ = ["app"]
```

- [ ] **Step 4.11:把 `app.py` 改成 shim**

```python
"""薄入口 shim — 真正的实现已搬到 app/ 包。

保留是为了:
1. uvicorn app:app 启动方式不变
2. start.sh 里 `uvicorn app:app` 不改
"""
from app.main import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

- [ ] **Step 4.12:冒烟测试**

```bash
# 1. import 检查
python -c "from app import app; print('ok, routes:', len(app.routes))"

# 2. 启动 dashboard(后台跑)
./start.sh dashboard

# 3. 健康检查
curl -s http://localhost:8000/healthz
# Expected: {"status":"ok"} 或类似

# 4. 手动验证(浏览器或 curl)
# - 访问 http://localhost:8000/ 应跳 /login
# - 登录后能看到 5 个 tab
# - 文件浏览、记事本 CRUD 跑通(抽 1-2 个测)

# 5. 关掉
kill $(cat logs/dashboard.pid)
```

- [ ] **Step 4.13:Commit**

```bash
git add app/ app.py
git commit -m "refactor: 拆 app.py (2045行) → app/ 包 (按域分 router)

- app/main.py: create_app + middleware
- app/routes/*.py: 按域分(auth/dashboard/files/notebook/zvideo/shortcut/proxy)
- app/{perf,cocoa,zstatus,shortcut_client,nas_helpers}.py: 共享 helper
- app.py 改成 shim,uvicorn app:app 和 start.sh 零改动
- nas/ 提供加密协议层(不再重复 RSA PEM)"
```

---

### Task 5:更新 README + MCP.md(导入路径相关)

**Files:**
- Modify: `README.md`(更新「目录结构」「开发」相关段落)
- Modify: `MCP.md`(如果有提到 mcp_server.py 内部结构的段落)

**变更要点**:
- README 的"目录结构"段(若有)更新成新结构
- "开发"段加一句:"修改 tool 请编辑 `mcp_server/tools/<域>.py`,不要往 mcp_server.py 加(tool 实现已搬走)"
- 不动 API.md(端点表不变)、不动 start.sh(入口不变)

- [ ] **Step 5.1:Edit `README.md`**

在 README 第 1 段「两部分」后,加一个「目录结构」小节,粘贴本 plan 顶部的 File Structure 树。

- [ ] **Step 5.2:Edit `MCP.md`**

搜索 MCP.md 里所有 `mcp_server.py` 的引用,改成 `mcp_server/main.py` 或 `mcp_server/tools/<域>.py`(按上下文)。

```bash
grep -n "mcp_server\.py" MCP.md
```

逐条判断后 edit。

- [ ] **Step 5.3:Commit**

```bash
git add README.md MCP.md
git commit -m "docs: 更新 README/MCP.md 反映拆分后的目录结构"
```

---

## Phase 2:文件诊断 skill(只读)

### Task 6:`.claude/skills/file-organizer/` 脚手架

**Files:**
- Create: `.claude/skills/file-organizer/SKILL.md`
- Create: `.claude/skills/file-organizer/README.md`
- Create: `.claude/skills/file-organizer/file_organizer.py`
- Create: `.claude/skills/file-organizer/lib/nas_client.py`(桥接层)

**Interfaces:**
- 脚本 CLI:`python file_organizer.py <command> [options]`
  - 命令:`audit-duplicates`(找重复文件)、`audit-orphans`(找孤儿文件)、`audit-all`(两个一起)
  - 选项:`--pool <name>`(限定池,默认扫所有池的 `/my/` 下)、`--output <path>`(写 JSON)、`--sample <n>`(限制扫描条数,用于测试)、`--min-size <MB>`(忽略小于此大小的文件,默认 1MB)
- 输出格式:JSON 写 `--output` 文件,文本摘要写 stdout

- [ ] **Step 6.1:建目录**

```bash
mkdir -p .claude/skills/file-organizer/lib
```

- [ ] **Step 6.2:写 `lib/nas_client.py`**

完全照抄 `.claude/skills/media-organizer/lib/nas_client.py`(就是桥接到 `mcp_server.NasClient`)。**但 NasClient 是 async 的,skill 脚本是同步的**——所以这里要包一层同步适配器:

```python
"""桥接层:从 mcp_server.NasClient 包装出同步接口。

复用 RSA 登录 + cookie 管理,不重复实现。
"""
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT))


def _load_env():
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        raise SystemExit(
            f"❌ 找不到 {env_file}\n"
            "   请先 cp .env.example .env 并填好 NAS_USER / NAS_PASSWORD"
        )
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

from mcp_server import NasClient  # noqa: E402

# 全局 client + event loop(脚本同步调用)
_client = None
_loop = None


def _get_client() -> NasClient:
    global _client, _loop
    if _client is None:
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _client = NasClient()
        _loop.run_until_complete(_client.login())
    return _client


def run(coro):
    """同步包装 async 调用。"""
    return _loop.run_until_complete(coro)


def get(path: str):
    return run(_get_client().get(path))


def post(path: str, data: dict | None = None):
    return run(_get_client().post(path, data or {}))


__all__ = ["NasClient", "PROJECT_ROOT", "get", "post", "run"]
```

- [ ] **Step 6.3:验证桥接**

```bash
cd .claude/skills/file-organizer
python -c "import sys; sys.path.insert(0, 'lib'); import nas_client; print(nas_client.NasClient)"
```
Expected: `<class 'mcp_server.NasClient'>` 或 `nas.client.NasClient`(取决于 __init__.py 重导出路径)。

- [ ] **Step 6.4:Commit**

```bash
git add .claude/skills/file-organizer/lib/
git commit -m "feat(file-organizer): 加 skill 脚手架(lib/nas_client.py 桥接层)"
```

---

### Task 7:写 `file_organizer.py` 主脚本(重复文件检测)

**Files:**
- Modify: `.claude/skills/file-organizer/file_organizer.py`(完整实现)

**核心逻辑**:
1. 用 `list_storage_pools` 拿到所有池
2. 对每个池,从 `/my/` 起 DFS 遍历(`list_files` 每 200 一批)
3. 对每个文件:
   - 跳过 < `--min-size`(默认 1MB)
   - 记录 `(size, ext)` 作为快速分组键
   - 对每个 `(size, ext)` 组,组内 >1 个元素时计算 MD5(调 `/v2/file/info` 或新加 chunk hash 端点)
   - 同 MD5 即重复

**关键约束**:
- NAS `list_files` 一次最多 200 条,要分页
- NAS 没暴露"计算文件 hash"端点 —— 实测用 `/v2/file/info` 是否返回 hash 字段?需要先验证。若没有,改用 `(size, name)` 作为弱指纹(误报高,但只读安全)
- 大文件不要全部下到本地算 hash(太慢) —— 用 size+name+ext 作粗指纹

**Interfaces:**
- 函数:`scan_duplicates(pool: str, min_size_mb: int = 1) -> list[dict]`
  - 返回 `[{"size": int, "md5_or_size_key": str, "paths": [...], "count": int}, ...]`

- [ ] **Step 7.1:验证 NAS 端点是否返回 hash**

```bash
# 实跑 list_files 看返回字段
python -c "
from mcp_server import NasClient
import asyncio
async def m():
    nas = NasClient()
    await nas.login()
    r = await nas.post('/v2/file/list', {'folderId': 0, 'path': '/sata14/my/', 'start': 0, 'num': 5, 'sortby': 'name', 'order': 'asc', 'show_hidden': 0})
    import json
    print(json.dumps(r.get('data', {}).get('list', [{}])[0], indent=2, ensure_ascii=False))
asyncio.run(m())
"
```

如果返回里有 `md5` / `hash` / `sha` 字段 → 直接用。否则降级到 `(size, name, ext)`。

- [ ] **Step 7.2:写主脚本骨架**

```python
#!/usr/bin/env python3
"""file_organizer.py — NAS 文件诊断(只读)。

复用 media-organizer 的桥接模式,扫 NAS 找重复文件 + 孤儿文件。
不动 NAS,只生成报告。
"""
import argparse
import json
import sys
from pathlib import Path

# 桥接 NAS client(同步接口)
sys.path.insert(0, str(Path(__file__).parent / "lib"))
import nas_client  # noqa: E402


def cmd_audit_duplicates(args):
    """扫所有池,按 hash/size 分组找重复。"""
    pools_resp = nas_client.post("/zspool/info", {})
    # ... 详实现 ...


def cmd_audit_orphans(args):
    """扫所有文件,找无标签 + 不属于任何影视分类源的"野生"文件。"""
    # 1. 拿所有标签 list_file_labels
    # 2. 拿所有文件 save_file_label 关联(或反向查)
    # 3. 拿所有 zvideo classification dirs(list_video_dirs)
    # 4. DFS 扫所有池的文件,排除在 classification dirs 下的
    # 5. 输出无标签 + 非影视的文件清单
    pass


def main():
    p = argparse.ArgumentParser(description="NAS 文件诊断(只读)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd_name in ("audit-duplicates", "audit-orphans", "audit-all"):
        sp = sub.add_parser(cmd_name)
        sp.add_argument("--pool", default="", help="限定池名,默认扫所有池")
        sp.add_argument("--output", default="", help="写 JSON 到文件")
        sp.add_argument("--sample", type=int, default=0, help="限制扫描条数(测试用)")
        sp.add_argument("--min-size", type=int, default=1, help="忽略小于 N MB 的文件")
    args = p.parse_args()

    if args.cmd in ("audit-duplicates", "audit-all"):
        result = cmd_audit_duplicates(args)
        _emit(result, args)
    if args.cmd in ("audit-orphans", "audit-all"):
        result = cmd_audit_orphans(args)
        _emit(result, args)


def _emit(result, args):
    text = _format_text(result)
    print(text)
    if args.output:
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 7.3:实现 `cmd_audit_duplicates`**

完整实现(基于 Step 7.1 验证后的字段):

```python
def cmd_audit_duplicates(args):
    """按 (size, ext) 或 (md5) 分组找重复。

    策略:若 NAS list_files 返回 hash → 用 hash 精确;否则用 (size, ext) 弱指纹。
    """
    pools = nas_client.post("/zspool/info", {}).get("data", {}).get("pools", [])
    groups = {}  # fingerprint -> [paths]
    total_scanned = 0

    for pool in pools:
        if args.pool and pool.get("name") != args.pool:
            continue
        pool_name = pool.get("name", "unknown")
        root = f"/{pool_name}/my/"
        total_scanned += _dfs_scan(root, args, groups)

    duplicates = [
        {"fingerprint": fp, "count": len(paths), "paths": paths}
        for fp, paths in groups.items()
        if len(paths) > 1
    ]
    duplicates.sort(key=lambda x: x["count"], reverse=True)

    return {
        "cmd": "audit-duplicates",
        "strategy": "hash" if _nas_has_hash() else "size+ext",
        "total_scanned": total_scanned,
        "duplicate_groups": len(duplicates),
        "duplicates": duplicates[:200],  # 截前 200 组,避免输出爆炸
    }


def _dfs_scan(root: str, args, groups: dict) -> int:
    """DFS 扫目录,每 200 条分页。返回扫描文件数。"""
    start = 0
    scanned = 0
    while True:
        if args.sample and scanned >= args.sample:
            break
        resp = nas_client.post("/v2/file/list", {
            "folderId": 0, "path": root, "start": start, "num": 200,
            "sortby": "name", "order": "asc", "show_hidden": 0,
        })
        items = resp.get("data", {}).get("list", []) or []
        if not items:
            break
        for item in items:
            if item.get("type") == "folder":
                scanned += _dfs_scan(root + item["name"] + "/", args, groups)
            else:
                size = item.get("size", 0)
                if size < args.min_size * 1024 * 1024:
                    continue
                fp = _fingerprint(item)
                groups.setdefault(fp, []).append(root + item["name"])
                scanned += 1
        if len(items) < 200:
            break
        start += 200
    return scanned


def _fingerprint(item: dict) -> str:
    """优先用 hash,降级用 size+ext。"""
    for key in ("md5", "hash", "sha"):
        v = item.get(key)
        if v:
            return f"{key}:{v}"
    name = item.get("name", "")
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return f"size:{item.get('size', 0)},ext:{ext}"


_NAS_HAS_HASH_CACHE = None
def _nas_has_hash() -> bool:
    global _NAS_HAS_HASH_CACHE
    if _NAS_HAS_HASH_CACHE is None:
        r = nas_client.post("/v2/file/list", {
            "folderId": 0, "path": "/sata14/my/", "start": 0, "num": 1,
            "sortby": "name", "order": "asc", "show_hidden": 0,
        })
        items = r.get("data", {}).get("list", []) or []
        _NAS_HAS_HASH_CACHE = any(items and items[0].get(k) for k in ("md5", "hash", "sha"))
    return _NAS_HAS_HASH_CACHE
```

- [ ] **Step 7.4:实现 `cmd_audit_orphans`**

```python
def cmd_audit_orphans(args):
    """找无标签 + 不属于任何影视分类源的"野生"文件。

    定义:
    - 在 classification 关联目录下的文件 → 影视相关,跳过
    - 有标签的文件 → 已分类,跳过
    - 其余 → 孤儿文件(候选整理目标)
    """
    # 1. 拿所有 zvideo 分类关联的源目录
    dirs_resp = nas_client.post("/zvideo/classification/dirs", {})
    video_dirs = set(dirs_resp.get("data", []) or [])

    # 2. 拿所有有标签的文件路径(label 反查端点:实测可能要遍历,见已知 gap)
    labeled_paths = set()
    labels_resp = nas_client.post("/v2/label/list", {})  # 字段名待验证
    # ... depends on NAS API shape ...

    # 3. DFS 扫所有池,标记每条文件的归属
    orphans = []
    pools = nas_client.post("/zspool/info", {}).get("data", {}).get("pools", [])
    for pool in pools:
        root = f"/{pool.get('name')}/my/"
        orphans.extend(_scan_orphans(root, video_dirs, labeled_paths, args))

    return {
        "cmd": "audit-orphans",
        "total_orphans": len(orphans),
        "video_dirs_count": len(video_dirs),
        "labeled_count": len(labeled_paths),
        "orphans": orphans[:500],  # 截前 500
    }
```

**已知 gap**:`list_file_labels` 返回的是标签定义,不是文件↔标签关联。要拿"已打标签的文件清单"需要新端点(待 Step 7.1 验证)。若拿不到 → orphan 报告退化为"非影视目录下的文件清单",仍有价值。

- [ ] **Step 7.5:实跑 + 验证输出**

```bash
cd .claude/skills/file-organizer

# 测试 1:帮助
python file_organizer.py --help

# 测试 2:小样本(限制 50 条)
python file_organizer.py audit-duplicates --sample 50 --min-size 10
# Expected: 文本摘要 + JSON(若有 --output)

# 测试 3:全量重复扫描
python file_organizer.py audit-duplicates --output /tmp/dups.json
# Expected: 写 JSON 到 /tmp/dups.json,stdout 是文本摘要

# 测试 4:孤儿扫描
python file_organizer.py audit-orphans --output /tmp/orphans.json
```

- [ ] **Step 7.6:Commit**

```bash
git add .claude/skills/file-organizer/file_organizer.py
git commit -m "feat(file-organizer): 实现 audit-duplicates + audit-orphans 扫描

- audit-duplicates: 按 hash 或 (size, ext) 找重复文件
- audit-orphans: 找非影视目录 + 无标签的孤儿文件
- 输出 stdout 文本摘要 + --output JSON 详细清单
- 只读,不动 NAS"
```

---

### Task 8:写 `SKILL.md` + `README.md`

**Files:**
- Create: `.claude/skills/file-organizer/SKILL.md`
- Create: `.claude/skills/file-organizer/README.md`

照 `.claude/skills/media-organizer/SKILL.md` 的格式。

- [ ] **Step 8.1:写 `SKILL.md`**

```markdown
---
name: file-organizer
description: 诊断 ZSpace NAS 文件库,找出重复文件 + 孤儿文件(只读,不动 NAS)。
  触发词:重复文件、孤儿文件、文件整理诊断、哪些文件重复了、哪些文件没归类、找重复电影、找重复照片。
  不适用:写操作(删除重复 / 移动孤儿)— 这些走 MCP tool(remove/move/save_file_label)+ LLM 二次确认,不在这 skill 范围。
---

# File Organizer — NAS 文件只读诊断

## 概述

通过组合 NAS MCP tool + `file_organizer.py` 脚本,**只读**诊断文件库的"重复"和"孤儿"问题。
**所有写操作都不在 skill 里** — 报告生成后,用户手动决定怎么处理。

## 2 个诊断命令

| 命令 | 用途 | 走哪个端点 |
|------|------|-----------|
| `audit-duplicates` | 扫全盘找重复文件(按 hash 或 size+ext) | `/v2/file/list` + `/zspool/info` |
| `audit-orphans` | 找无标签 + 非影视目录的孤儿文件 | `/v2/file/list` + `/zvideo/classification/dirs` + `/v2/label/*` |

## 工作流

### 场景 1:用户说"我 NAS 上有没有重复文件"

**步骤**:
1. **exec** `python .claude/skills/file-organizer/file_organizer.py audit-duplicates --output /tmp/dups.json`
2. 读 stdout 摘要 + JSON 详细清单
3. 报告:总扫描 N 个文件,发现 X 组重复,共 Y 个冗余文件,占用 Z GB
4. 列出前 10 组最大的重复(用户优先处理收益大的)

### 场景 2:用户说"哪些文件没归类"

**步骤**:
1. **exec** `python .claude/skills/file-organizer/file_organizer.py audit-orphans --output /tmp/orphans.json`
2. 报告:总扫描 N 个文件,X 个孤儿(无标签 + 非影视)
3. 建议处理:
   - 大文件孤儿 → 考虑删除或归档
   - 文档/图片孤儿 → 建议打标签(save_file_label)
   - 临时/缓存文件 → 候选清理

## 关键约束(必读)

1. **只读诊断,不动 NAS** — 这个 skill 是"找出问题",不是"修复"
2. **修复走 MCP tool** — 删/移/打标 用现有 MCP `remove` / `move` / `save_file_label`,LLM 弹 UI 让用户批
3. **`remove` 不进回收站,不可逆** — 处理重复文件时优先 `move` 到 `_to_review/`,人工确认后再删
4. **指纹策略** — NAS `list_files` 若返回 hash 字段 → 精确;否则用 (size, ext) 弱指纹,误报需人工核对
5. **扫描范围** — 默认所有池的 `/<pool>/my/` 下,跳过 < 1MB(可改 `--min-size`)

## 已知 gap

- 没法精确反查"哪些文件已打标签"(NAS 端点不暴露 label↔file 关联表),orphan 检测只能退化为"非影视目录 + 无标签 ID 集合"
- 大文件 hash 计算:NAS 没 chunk hash 端点,只能靠 list_files 返回的元数据(待 Step 7.1 验证)
- 跨用户共享目录(`/<pool>/share/`)不在扫描范围(默认只扫 `/<pool>/my/`)

## 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 扫描卡住 | NAS list_files 限流 | 加 `--sample 1000` 限制条数 |
| `audit-duplicates` 全是 size+ext 误报 | NAS 不返回 hash | 报告里说明,人工核对;或后续加 MD5 计算(走 /v2/file/download 拉到本地算,慢) |
| `audit-orphans` 报错"找不到 label 端点" | NAS 端点未破 | 降级到"非影视目录下的文件清单",报告标注 |

## 后续可以做(等用户要求再加)

- **执行 agent**:升级为弹 UI 二次确认后真删/真移(skill 外,走 MCP tool)
- **照片按 EXIF 整理**:用 EXIF 时间线分组,建议命名规范
- **冷热分层**:基于 `recent_files` 找长期未访问的,建议从 SSD 移到 HDD
- **大文件审查**:专门找 >10GB 的文件,分类列出
```

- [ ] **Step 8.2:写 `README.md`(简短)**

```markdown
# File Organizer Skill

NAS 文件只读诊断:找重复文件 + 孤儿文件。

详细见 `SKILL.md`。脚本入口:

```bash
python file_organizer.py audit-duplicates --output /tmp/dups.json
python file_organizer.py audit-orphans --output /tmp/orphans.json
python file_organizer.py audit-all --output /tmp/full-report.json
```

复用 media-organizer 的 lib/nas_client.py 桥接模式,通过 `from mcp_server import NasClient` 复用登录逻辑。
```

- [ ] **Step 8.3:Commit**

```bash
git add .claude/skills/file-organizer/SKILL.md .claude/skills/file-organizer/README.md
git commit -m "docs(file-organizer): 写 SKILL.md + README,2 个诊断命令文档化"
```

---

## Self-Review

### Spec coverage(用户原始需求 → task 映射)

| 用户需求 | 对应 task |
|---|---|
| "拆分出合理的路由,避免单文件过大" | Task 1-5(抽公共层 + 拆 mcp_server + 拆 app + 更新文档) |
| "深化某一个场景的 agent" | Task 6-8(file-organizer skill,只读诊断) |
| "先做文件的" | file-organizer 选了文件域,而非记事本/影视 |
| "先做只读诊断 skill" | audit-duplicates + audit-orphans,不动 NAS |

### Placeholder scan

- Task 7 Step 7.4 的 `list_file_labels` 反查部分有 "字段名待验证" 标注 —— 这是**真实不确定性**(NAS API 没破),plan 里给了降级方案(退化为非影视目录扫描)。不算占位符,是诚实标注。
- 其他步骤都有具体代码或映射表,无 TBD。

### Type consistency

- `NasClient` 类名贯穿所有 task 一致
- `nas_client.get/post/run` 函数名在 Task 6 定义、Task 7 调用,签名一致(`get(path: str)`, `post(path: str, data: dict | None)`, `run(coro)`)
- `audit-duplicates` / `audit-orphans` 命令名在 SKILL.md、CLI、Task 7 实现一致
- `--pool/--output/--sample/--min-size` 选项名贯穿一致

### 潜在风险

1. **循环 import**:`mcp_server/tools/*.py` import `mcp_server.main`,而 `mcp_server.main` 在末尾 import `mcp_server.tools.*`。Python 模块缓存能处理,但启动顺序要测试(Task 3 Step 3.10 冒烟覆盖)
2. **app.py 的 session 依赖**:`_nas_get`/`_nas_post` 接收 `httpx.AsyncClient`,在 FastAPI route 里每次请求构造,搬动后要确保 session cookies 传递正确(Task 4 Step 4.12 浏览器测试覆盖)
3. **NAS 端点不确定性**(Task 7 Step 7.1):用验证步骤 + 降级策略覆盖

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-11-refactor-and-file-skill.md`. Two execution options:**

**1. Subagent-Driven(推荐)** — 我每个 task 派一个新 subagent,两阶段评审(自动 + 我)。适合本 plan:每个 task 边界清晰,适合并行/隔离执行。

**2. Inline Execution** — 我在本会话用 executing-plans 跑,带 checkpoint 让你 review。适合:你想边看边调,不放心就停下。

**哪个?**
