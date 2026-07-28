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
import secrets
import sys
from typing import TYPE_CHECKING, Optional

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from nas import NasClient

if TYPE_CHECKING:  # pragma: no cover
    from zspace.mcp_server.zenith import ZenithSession

from zspace.mcp_server.auth import StaticTokenVerifier  # noqa: E402

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
        # 不要让登录失败杀掉整个 server：stdio 场景下进程直接退出，
        # 用户连 N001414(新设备验证) 的引导信息都看不到，MCP 宿主只会报"连接失败"。
        # 保持运行——tools 都是 lazy-login（get/post 内未登录先 login），
        # 首次调用会重试并把 N001414 等错误作为 tool 结果返回给 agent，
        # 用户反而能在客户端里看到完整的绑定引导。
        log.warning(
            "startup login failed (server keeps running; first tool call will retry): %s", e
        )


def main(
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: Optional[int] = None,
) -> None:
    """启动 MCP server。transport ∈ {"stdio", "sse", "streamable-http"}。

    HTTP 路径(streamable-http)需要 `MCP_HTTP_TOKEN` env:没设就自动生成
    `secrets.token_hex(32)` 并 log 提示用户钉进 .env。同一进程同一 FastMCP 实例,
    通过 mutate `mcp.settings` / `mcp._token_verifier` 切换 transport 配置。
    """
    if transport == "streamable-http":
        _configure_http(mcp, host=host, port=port or 8765)

    asyncio.run(_startup())
    log.info(
        "MCP server 'zspace-nas' starting, transport=%s, %d tools registered",
        transport, len(mcp._tool_manager._tools),
    )
    mcp.run(transport=transport)  # host/port 已 mutate 进 settings


def _configure_http(mcp_instance: FastMCP, host: str, port: int) -> None:
    """为 HTTP transport 注入鉴权 + LAN-friendly transport_security。

    在 tool imports 之后、`mcp.run()` 之前调用。FastMCP 在 run_streamable_http_async
    里才读 settings.host/port 与 self._token_verifier,所以 mutate 安全。
    """
    import os

    token = os.environ.get("MCP_HTTP_TOKEN", "").strip()
    if not token:
        token = secrets.token_hex(32)
        log.warning(
            "MCP_HTTP_TOKEN 未设置,自动生成一次性 token: %s\n"
            "  → 想钉死请把这一行复制进 .env: MCP_HTTP_TOKEN=%s",
            token, token,
        )
    else:
        log.info("MCP_HTTP_TOKEN 已设置,长度=%d 字符", len(token))

    mcp_instance.settings.host = host
    mcp_instance.settings.port = port
    mcp_instance.settings.auth = AuthSettings(
        issuer_url="http://placeholder.invalid",
        resource_server_url=f"http://placeholder.invalid:{port}",
        required_scopes=[],
    )
    mcp_instance._token_verifier = StaticTokenVerifier(token)
    mcp_instance.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,  # LAN 必须关
        allowed_hosts=["*"],
        allowed_origins=["*"],
    )


if __name__ == "__main__":
    main()
