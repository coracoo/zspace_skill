"""NAS HTTP 协议层:base URL + 公共 query 参数(axaxios 拦截器追加)。"""
import os

NAS_BASE = os.environ.get("NAS_BASE", "")


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
