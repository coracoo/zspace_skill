"""MCP server 入口:FastMCP 实例 + 启动逻辑。

全局变量:
- `mcp`   — FastMCP 实例(永不重新赋值,所有 tool 文件 `from zspace.mcp_server.main import mcp` 装饰)
- `nas`   — NasClient 实例(_startup 中赋值);tool 函数通过 `from zspace.mcp_server import main as _main; _main.nas` 在调用时取最新值
- `zenith`— ZenithSession 实例(lazy init,proxy_login/proxy_fetch 触发)

循环 import 拓扑:
    main.py 定义 mcp + nas → import tools/*.py → tools 文件 import mcp_server.main
    关键:本文件里 `from zspace.mcp_server.tools import ...` 必须在 `mcp = FastMCP(...)` 之后,
    否则 tools 文件 import 时拿不到 mcp,装饰器报 NameError。
"""
import asyncio
import logging
import sys
from typing import TYPE_CHECKING, Optional

from mcp.server.fastmcp import FastMCP

from zspace.nas import NasClient

if TYPE_CHECKING:  # pragma: no cover
    from zspace.mcp_server.zenith import ZenithSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,  # MCP 用 stdout 通信,log 必须 stderr
)
log = logging.getLogger("zspace-mcp")

# ---- FastMCP 实例(必须在 tool import 之前定义)----
mcp = FastMCP("zspace-nas")

# ---- 全局实例(_startup / proxy_login 里赋值)----
# 注意:tool 函数不能 `from zspace.mcp_server.main import nas`(那是值绑定,会停在 None);
# 必须用 `from zspace.mcp_server import main as _main; _main.nas.post(...)`(属性访问,运行时取最新值)
nas: Optional[NasClient] = None
zenith: Optional["ZenithSession"] = None


# ---- 触发所有 @mcp.tool() 注册(import 即注册)----
from zspace.mcp_server.tools import (  # noqa: E402,F401
    files,
    storage,
    zvideo,
    media,
    shares,
    notebook,
    proxy,
    znetdisk,
)
from zspace.mcp_server.rag_hook import _rag_hook  # noqa: E402,F401

# 可选 RAG tool(包未安装时静默跳过;tools/rag.py 自己 try-import rag.mcp_tools
# 并在成功时把模块塞到 mcp_server.rag_hook._rag_tools / _HAS_RAG)
try:
    from zspace.mcp_server.tools import rag  # noqa: E402,F401
except ImportError as e:
    log.warning("RAG module not available, skipping 3 rag tools: %s", e)


async def _startup():
    """登录 NAS,实例化 zenith session(沿用原 mcp_server.py:1263-1276 的语义,
    无 ZENITH_COOKIE 时 zenith 仍初始化(空 cookie header),方便 proxy_* 工具 lazy 用)。"""
    global nas, zenith
    from zspace.mcp_server.zenith import ZenithSession, ZENITH_COOKIE_EXTRA
    nas = NasClient()
    try:
        await nas.login()
        zenith = ZenithSession(nas)
        log.info("zenith session ready, cookie_count=%d, has_extra=%s",
                 zenith._cookie_header.count(";") + 1 if zenith._cookie_header else 0,
                 bool(ZENITH_COOKIE_EXTRA))
    except Exception as e:
        log.error("startup login failed: %s", e)
        raise


def main():
    asyncio.run(_startup())
    log.info("MCP server 'zspace-nas' starting, %d tools registered", len(mcp._tool_manager._tools))
    mcp.run()


if __name__ == "__main__":
    main()
