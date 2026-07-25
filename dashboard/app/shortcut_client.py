"""iPhone Shortcut 用的 service-account 风格 NAS client(独立于 web session)。

搬迁自 app.py:783-880。关键差异:
- web session 的 httpx.AsyncClient 用用户登录 cookies,跟随会话生命周期
- shortcut client 用 env NAS_USER/NAS_PASSWORD 登录,全局缓存,5 秒锁防并发重登
- 两者**完全独立**,不要混用
"""
import asyncio
import logging
import os
import re
from typing import Dict, Optional

import httpx

from nas import NAS_BASE, encrypt_field, resolve_device_id

log = logging.getLogger("zspace-poc")

_shortcut_nas_client: Optional[httpx.AsyncClient] = None
_shortcut_nas_lock = asyncio.Lock()


async def get_shortcut_nas_client() -> Optional[httpx.AsyncClient]:
    """Service-account 风格:首次用 NAS_USER/NAS_PASSWORD env 登录 NAS,缓存 client 给后续用。
    cookies 在 NAS 超时才会失效,届时需要重置(see reset_shortcut_nas_client)。
    """
    global _shortcut_nas_client
    if _shortcut_nas_client is not None:
        return _shortcut_nas_client
    async with _shortcut_nas_lock:
        if _shortcut_nas_client is not None:
            return _shortcut_nas_client
        nas_user = os.environ.get("NAS_USER", "").strip()
        nas_pass = os.environ.get("NAS_PASSWORD", "").strip()
        if not nas_user or not nas_pass:
            log.error("SHORTCUT: NAS_USER/NAS_PASSWORD not set")
            return None
        device_id = resolve_device_id()
        # 跟 dashboard login_submit 完全一致:用 plaintext form-urlencoded 给 httpx,
        # 显式构造 cookies(包含 token + 全部 resp.cookies)。
        client = httpx.AsyncClient(timeout=10)
        form = {
            "username": encrypt_field(nas_user),
            "password": encrypt_field(nas_pass),
            "plat": "web",
            "device": "linux",
            "device_id": device_id,
        }
        try:
            resp = await client.post(f"{NAS_BASE}/auth/login", data=form)
        except httpx.HTTPError as e:
            log.error("SHORTCUT: NAS login HTTP error %s", e)
            await client.aclose()
            return None
        try:
            body = resp.json()
        except Exception:
            await client.aclose()
            return None
        if str(body.get("code")) != "200":
            log.error("SHORTCUT: NAS login rejected %s", body)
            await client.aclose()
            return None
        # 显式组装 cookies(dashboard /login 里就是这么干的,不开这个会 403)
        data = body.get("data") or {}
        explicit_cookies: Dict[str, str] = {
            "token": data.get("token", ""),
            "username": nas_user,
            "device_id": device_id,
            "device": "linux",
            "plat": "web",
        }
        for ck, cv in resp.cookies.items():
            explicit_cookies[ck] = cv
        # 重建 client with explicit cookies,丢掉 client 内置的 jar
        await client.aclose()
        client = httpx.AsyncClient(timeout=10, cookies=explicit_cookies)
        _shortcut_nas_client = client
        log.info("SHORTCUT: NAS login ok, session cached (token=%.8s...)", explicit_cookies["token"])
        return client


async def reset_shortcut_nas_client() -> None:
    """丢弃缓存的 shortcut client(用于 token 失效后强制下次重登)。

    取代旧的"只能重启 app"恢复方式。
    """
    global _shortcut_nas_client
    async with _shortcut_nas_lock:
        old = _shortcut_nas_client
        _shortcut_nas_client = None
    if old is not None:
        try:
            await old.aclose()
        except Exception:
            pass
        log.info("SHORTCUT: cached client reset (will re-login on next request)")


def title_eq(a: str, b: str) -> bool:
    """同名查重的"等价"判断:emoji 在 NAS 里两种形态都可能出现
    (UTF-8 字符 🐶 vs entity &#128054;),但语义上是同一条。
    两边都规整到 entity 形式再比,避免重复备份。
    """
    if not a or not b:
        return a == b
    if a == b:
        return True

    def _to_entity(s: str) -> str:
        return re.sub(r"[^\x00-\x7f]", lambda m: f"&#{ord(m.group(0))};", s)

    try:
        return _to_entity(a) == _to_entity(b)
    except Exception:
        return False
