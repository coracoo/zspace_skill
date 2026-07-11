"""NasClient:登录态 + httpx.AsyncClient,token 失效自动重登。

从 mcp_server.py 抽出。原样保留 token 续期 + 连接池化逻辑,只改内部调用名:
- _encrypt  → encrypt_field(来自 .auth)
- _common_query() → common_query(self._device_id)(来自 .proto)
"""
import asyncio
import logging
import os
from typing import Optional

import httpx

from .auth import encrypt_field, resolve_device_id
from .proto import NAS_BASE, common_query

log = logging.getLogger("zspace-mcp")

NAS_USER = os.environ.get("NAS_USER", "")
NAS_PASSWORD = os.environ.get("NAS_PASSWORD", "")


class NasClient:
    """登录态 + httpx.AsyncClient,token 失效自动重登"""

    def __init__(self):
        if not NAS_USER or not NAS_PASSWORD:
            raise RuntimeError("NAS_USER / NAS_PASSWORD env not set")
        self._device_id = resolve_device_id()
        self._client: Optional[httpx.AsyncClient] = None
        self._cookies: dict = {}
        self._profile: dict = {}
        self._logged_in = False
        self._login_lock = asyncio.Lock()

    async def _ensure_client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15)

    async def _maybe_relogin(self, response_data: dict) -> bool:
        """检测 N001208(token 失效),加锁串行重登,返回是否重登过。

        多个并发请求同时看到 token 失效时,锁保证只重登一次,
        其他请求等重登完成后直接用新 token。"""
        if str(response_data.get("code")) != "N001208" or not self._logged_in:
            return False
        async with self._login_lock:
            # 二次检查:可能其他请求已经完成了重登
            if str(response_data.get("code")) != "N001208":
                return False
            log.warning("token expired, re-logging in")
            await self.login()
            return True

    async def login(self) -> dict:
        """RSA 加密登录,返回 profile"""
        await self._ensure_client()
        form = {
            "username": encrypt_field(NAS_USER),
            "password": encrypt_field(NAS_PASSWORD),
            "plat": "web",
            "device": "linux",
            "device_id": self._device_id,
        }
        log.info("logging in user=%s", NAS_USER)
        resp = await self._client.post(f"{NAS_BASE}/auth/login", data=form)
        body = resp.json()
        if str(body.get("code")) != "200":
            raise RuntimeError(f"login failed: code={body.get('code')} msg={body.get('msg')}")
        data = body["data"]
        self._cookies = {
            "token": data.get("token", ""),
            "username": NAS_USER,
            "device_id": self._device_id,
            "device": "linux",
            "plat": "web",
        }
        for ck, cv in resp.cookies.items():
            self._cookies[ck] = cv
        self._profile = data
        self._logged_in = True
        log.info("login ok user=%s id=%s nickname=%s",
                 NAS_USER, data.get("id"), data.get("nickname"))
        return data

    async def get(self, path: str) -> dict:
        if not self._logged_in:
            await self.login()
        assert self._client is not None
        sep = "&" if "?" in path else "?"
        url = f"{NAS_BASE}{path}{sep}plat=web&version=2.3.2026062201&device_id={self._device_id}&device=linux&_l=zh-CN"
        r = await self._client.get(url, cookies=self._cookies)
        try:
            data = r.json()
        except Exception:
            data = {"_status": r.status_code, "_raw": r.text[:300]}
        # token 失效:重登后重发一次
        if isinstance(data, dict) and await self._maybe_relogin(data):
            r = await self._client.get(url, cookies=self._cookies)
            try:
                data = r.json()
            except Exception:
                data = {"_status": r.status_code, "_raw": r.text[:300]}
        return data

    async def post(self, path: str, body: dict | None = None, as_form: bool = True) -> dict:
        if not self._logged_in:
            await self.login()
        assert self._client is not None
        url = f"{NAS_BASE}{path}{common_query(self._device_id)}"
        if as_form and isinstance(body, dict):
            r = await self._client.post(url, data=body or {}, cookies=self._cookies)
        else:
            r = await self._client.post(url, json=body or {}, cookies=self._cookies)
        try:
            data = r.json()
        except Exception:
            data = {"_status": r.status_code, "_raw": r.text[:300]}
        # token 失效:重登后重发一次
        if isinstance(data, dict) and await self._maybe_relogin(data):
            if as_form and isinstance(body, dict):
                r = await self._client.post(url, data=body or {}, cookies=self._cookies)
            else:
                r = await self._client.post(url, json=body or {}, cookies=self._cookies)
            try:
                data = r.json()
            except Exception:
                data = {"_status": r.status_code, "_raw": r.text[:300]}
        return data

    async def aclose(self):
        if self._client:
            await self._client.aclose()

    async def download_text(self, path: str, max_bytes: int = 100 * 1024) -> Optional[str]:
        """下载小文本文件,超过 max_bytes 返回 None。RAG 全文索引用。

        /v2/file/download 是 GET + path query,带 NAS cookie。
        二进制 / 超大文件返回 None(让 RAG 跳过)。"""
        if not self._logged_in:
            await self.login()
        assert self._client is not None
        url = f"{NAS_BASE}/v2/file/download?path={path}{common_query(self._device_id).replace('?', '&', 1)}"
        try:
            r = await self._client.get(url, cookies=self._cookies, timeout=10)
            if r.status_code != 200:
                return None
            if len(r.content) > max_bytes:
                return None
            try:
                return r.content.decode("utf-8")
            except UnicodeDecodeError:
                return None
        except Exception:
            return None

    async def download_bytes(self, path: str, max_bytes: int = 100 * 1024) -> Optional[bytes]:
        """下载文件原始字节(docx/pdf 等二进制)。RAG 全文索引用。

        /v2/file/download 是 GET + path query,带 NAS cookie。
        超 max_bytes 返回 None(让 RAG 跳过)。"""
        if not self._logged_in:
            await self.login()
        assert self._client is not None
        url = f"{NAS_BASE}/v2/file/download?path={path}{common_query(self._device_id).replace('?', '&', 1)}"
        try:
            r = await self._client.get(url, cookies=self._cookies, timeout=10)
            if r.status_code != 200:
                return None
            if len(r.content) > max_bytes:
                return None
            return r.content
        except Exception:
            return None
