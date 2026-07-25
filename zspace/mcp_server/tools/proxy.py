"""远程访问代理 tool 集合(4 个,走 zos 云代理)。

源:mcp_server.py:1168-1260

公网 URL 模板:https://remote-access-{port}.zconnect.cn/ → NAS 127.0.0.1:{port}
工作流:用户在白名单加端口 → zos 自动分配子域名 → 互联网可访问
这些工具让 MCP 客户端(Claude Code)在公网上也能访问 LAN 内 HTTP 服务。
"""
from zspace.mcp_server import main as _main
from zspace.mcp_server.main import mcp
from zspace.mcp_server.perf import _to_json
from zspace.mcp_server.zenith import ZenithSession, ZENITH_COOKIE_EXTRA


@mcp.tool()
async def proxy_login() -> str:
    """强制重新登录,刷新 zenith cloud session cookie(原 /auth/login 的 token)。

    调用场景:
    - 启动时 zenith session 初始化失败
    - 收到 401/403 from proxy_fetch(可能 token 过期)
    - 想换账号测试
    返回:登录 profile 摘要 + cookie 数量(完整 cookie 不暴露)。"""
    try:
        await _main.nas.login()
        _main.zenith = ZenithSession(_main.nas)
        return _to_json({
            "ok": True,
            "user_id": _main.nas._profile.get("id"),
            "username": _main.nas._profile.get("username"),
            "nickname": _main.nas._profile.get("nickname"),
            "is_master": _main.nas._profile.get("is_master"),
            "cookie_count": _main.zenith._cookie_header.count(";") + 1 if _main.zenith._cookie_header else 0,
            "has_extra_cookie": bool(ZENITH_COOKIE_EXTRA),
        })
    except Exception as e:
        return _to_json({"ok": False, "error": str(e)})


@mcp.tool()
async def proxy_url_for_port(port: int) -> str:
    """返回 zos 给定 NAS 端口分配的公网 URL 模板。

    port: NAS 本地端口号(白名单里的)
    返回:形如 https://remote-access-33335.zconnect.cn/

    注意:URL 是否真能访问,取决于白名单里是否有 `127.0.0.1:{port}` 或 `LAN_IP:{port}`。
    调用 proxy_fetch(port, "/") 可以验证。"""
    url = f"https://remote-access-{port}.zconnect.cn/"
    return _to_json({
        "port": port,
        "url": url,
        "note": "If 200 with empty body or login redirect, whitelist doesn't include 127.0.0.1:{port}.",
    })


@mcp.tool()
async def proxy_fetch(
    port: int, path: str = "/",
    method: str = "GET", body: str = "",
) -> str:
    """通过 zos 云代理从公网访问 NAS 内网 HTTP 服务。

    port: NAS 本地端口(白名单里的)
    path: 要请求的路径,默认 /
    method: HTTP 方法,默认 GET
    body: 请求 body(POST/PUT 时用,application/x-www-form-urlencoded)

    工作原理:把请求发到 https://remote-access-{port}.zconnect.cn/{path},
    zos 转发到 NAS 127.0.0.1:{port}(假设白名单有)。

    ⚠️ 已知 gap:
    - 白名单条目是 `LAN_IP:port`(如 `192.168.0.118:9876`)而不是 `127.0.0.1:port` 时,
      zos 可能拒绝或代理到错误的机器
    - 如果 cloud session cookie 不全(/auth/login 只给 token,不给 sign/cloudPubKey...),
      云代理可能直接 SPA HTML 回包;完整 cookie 需通过 ZENITH_COOKIE env 提供"""
    if _main.zenith is None:
        _main.zenith = ZenithSession(_main.nas)
    res = await _main.zenith.fetch(port, path, method, body)
    return _to_json(res)


@mcp.tool()
async def proxy_list_whitelist() -> str:
    """读 NAS 远程访问白名单(所有端口映射规则)。

    ⚠️ 已知 gap:NAS 上 /zrps/api/remoteaccess/list 和 /info 都返回 200 + 空 body
    (openresty 路由存在但后端不响应)。完整白名单只能从 pcweb UI 的"远程访问"页看。
    此工具返回登录 profile + 说明 gap。"""
    return _to_json({
        "gap": True,
        "msg": "/zrps/api/remoteaccess/{list,info,getInfo,...} all return 200+empty body. "
               "NAS openresty has the route but no backend response. "
               "View whitelist via pcweb UI → 远程访问.",
        "logged_in_as": _main.nas._profile.get("username"),
        "user_id": _main.nas._profile.get("id"),
        "nas_id": "Z0431212VNY4H",  # 来自 paste-cache 抓包,硬编码
        "tip": "Use proxy_url_for_port(port) to enumerate public URLs once you know the ports from pcweb UI.",
    })
