"""Zenith 云代理 session(走 zos 公网子域名访问 NAS LAN 端口)。

架构:
    用户点 pcweb 远程访问 → zconnect.cn 给每个内网端口分配子域名
    https://remote-access-{port}.zconnect.cn/  →  NAS 127.0.0.1:{port}

认证:
    zenith 云需要 session cookie(token/device_id/sign/nasId/nasPubKey/cloudPubKey...)。
    /auth/login 拿到的 token 跟 zenithtoken 是同 JWT,但其它 cloud cookie 不会自动给。
    用户可以从浏览器复制完整 cookie 字符串,设到 ZENITH_COOKIE env;
    或者只用 token 试试(部分端点可能够用)。

注:NasClient 类型用 forward ref(避免 main.py ↔ zenith.py 互相 import)。
"""
import os
import time
from typing import TYPE_CHECKING, Optional

import httpx

if TYPE_CHECKING:  # pragma: no cover
    from zspace.nas import NasClient

# 用户可从浏览器复制的完整 cloud cookie(填 device_id/sign/nasId/cloudPubKey 等)
ZENITH_COOKIE_EXTRA = os.environ.get("ZENITH_COOKIE", "").strip()
CLOUD_BASE_TPL = "https://remote-access-{port}.zconnect.cn"

# pcweb 拦截器给所有 POST 带的公共 query
PROXY_QUERY = "?&rnd={rnd}&webagent=v2"

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


class ZenithSession:
    """持有 cloud proxy 的 cookie 状态,转发请求到 remote-access-{port}.zconnect.cn"""

    def __init__(self, nas: "NasClient"):
        self.nas = nas
        self._cookie_header = ""
        self._refresh()

    def _refresh(self):
        cookies = dict(self.nas._cookies)  # 至少有 token/username/device_id
        # 叠加用户提供的完整 cookie(填 device_id/sign/nasId/cloudPubKey 等)
        if ZENITH_COOKIE_EXTRA:
            for part in ZENITH_COOKIE_EXTRA.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    cookies[k.strip()] = v.strip()
        # 填一些 pcweb 默认带的(没值也带上,云端可能依赖)
        cookies.setdefault("webagent", "v2")
        cookies.setdefault("_l", "zh_cn")
        cookies.setdefault("plat", "web")
        cookies.setdefault("app", "file")
        cookies.setdefault("device", "PC")
        cookies.setdefault("publicSwitch", "true")
        # 拼成单一 Cookie header
        self._cookie_header = "; ".join(
            f"{k}={v}" for k, v in cookies.items() if v
        )

    async def fetch(
        self, port: int, path: str,
        method: str = "GET", body: str = "",
        timeout: float = 10.0,
    ) -> dict:
        path = path if path.startswith("/") else "/" + path
        host = f"remote-access-{port}.zconnect.cn"
        rnd = f"{int(time.time()*1000)}_{os.getpid()}"
        url = f"{CLOUD_BASE_TPL.format(port=port)}{path}{PROXY_QUERY.format(rnd=rnd)}"
        # 每次请求重新拼装 cookie,避免 nas 重登后 token 过期
        self._refresh()
        cookie_hdr = self._cookie_header
        headers = {
            "Host": host,
            "Cookie": cookie_hdr,
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": f"https://{host}",
            "Referer": f"https://{host}/?v=2.3.2026062901",
        }
        if method.upper() != "GET" and body:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        async with httpx.AsyncClient(timeout=timeout, verify=False) as c:
            r = await c.request(
                method.upper(), url,
                headers=headers,
                content=body if body else None,
            )
            return {
                "_status": r.status_code,
                "content_type": r.headers.get("content-type", ""),
                "url": url,
                "headers_sent_count": len(headers),
                "cookie_count": cookie_hdr.count(";") + 1 if cookie_hdr else 0,
                "body": r.text[:4000],
            }
