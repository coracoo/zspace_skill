"""FastAPI 共享依赖:登录态校验 + 模板上下文。

搬迁自 app.py:531-544。改动:
- 原代码 `_require_login` 返回 (cookies, redirect) 二元组(redirect is None 表示 ok)
- 原代码 `_common_ctx` 接 (request, cookies) 返回模板上下文 dict

为减少改动面,保留原签名,只是改名字(去掉前导下划线方便外部 import)。
"""
from typing import Optional, Tuple

from fastapi import Request
from fastapi.responses import RedirectResponse


def require_login(request: Request) -> Tuple[Optional[dict], Optional[RedirectResponse]]:
    """检查 session 是否有 nas_cookies。

    返回 (cookies, None) 表示已登录;返回 (None, RedirectResponse) 表示未登录。
    保留二元组签名是为了减少 route 文件的改动量(原代码就是这模式)。
    """
    cookies = request.session.get("nas_cookies")
    if not cookies:
        return None, RedirectResponse("/login", status_code=303)
    return cookies, None


def common_ctx(request: Request, cookies: dict) -> dict:
    """所有 dashboard tab 模板共用的上下文。"""
    return {
        "user": request.session.get("nas_user", {}),
        "cookies_keys": list(cookies.keys()),
    }
