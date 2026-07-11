"""NAS HTTP helpers(基于 web session cookies,Dashboard 路由用)。

搬迁自 app.py:482-524。逻辑原样搬。

⚠️ 注意:本模块的 `append_common_query` 用的 device_id 是 nas.NAS_DEVICE_ID_DEFAULT
常量(与原 app.py 行为一致,不是 resolve_device_id())。这是为了保持与原 dashboard
URL 拼接的兼容性。如果要改成动态 device_id,需同步审查所有调用点。
"""
from typing import Any, Dict

import httpx

from nas import NAS_BASE, NAS_DEVICE_ID_DEFAULT


async def nas_get(client: httpx.AsyncClient, path: str) -> Dict[str, Any]:
    """用 web session 的 cookies GET 一个 NAS 接口,返回 JSON 或 status+raw 兜底。"""
    try:
        url = f"{NAS_BASE}{path}"
        url = append_common_query(url)
        r = await client.get(url)
        try:
            return r.json()
        except Exception:
            return {"_status": r.status_code, "_raw": r.text[:300]}
    except Exception as e:
        return {"_error": str(e)}


async def nas_post(
    client: httpx.AsyncClient, path: str, body: Any, as_form: bool = True
) -> Dict[str, Any]:
    """默认用 form-urlencoded(pcweb 默认),body 是 dict 自动展开。"""
    try:
        url = append_common_query(f"{NAS_BASE}{path}")
        if as_form and isinstance(body, dict):
            r = await client.post(url, data=body)
        else:
            r = await client.post(url, json=body)
        try:
            return r.json()
        except Exception:
            return {"_status": r.status_code, "_raw": r.text[:300]}
    except Exception as e:
        return {"_error": str(e)}


def append_common_query(url: str) -> str:
    """axios 拦截器给所有请求追加的公共参数(pcweb 默认行为)。

    用 NAS_DEVICE_ID_DEFAULT(代码常量),保留原 app.py 行为不变。
    """
    sep = "&" if "?" in url else "?"
    return (
        f"{url}{sep}plat=web"
        f"&version=2.3.2026062201"
        f"&device_id={NAS_DEVICE_ID_DEFAULT}"
        f"&device=linux"
        f"&_l=zh-CN"
    )
