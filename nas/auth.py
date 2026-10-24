"""NAS 登录加密层(RSA-PKCS1v15 + base64)。

4 处复用:dashboard/app.py、zspace/mcp_server.py、skills/*/lib/。
"""
import base64
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

# 注:这是 RSA 公钥(公开信息),用于登录加密,非私钥/密码。
# 【重要】不同固件版本的公钥不同,硬编码值会过期 —— 过期后登录返回
# N001200「账号格式不对」(实际是服务器解不开密文)。
# 因此 encrypt_field 优先从 NAS 实时端点 GET /key/pub?signme=1 拉取,
# 本常量仅作离线兜底。
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

# 实时公钥缓存(进程内 + 磁盘兜底)
_PUBKEY_RUNTIME = None
_PUBKEY_CACHE_FILE = Path.home() / ".cache" / "zspace-mcp" / "pubkey.pem"


def _fetch_pubkey_pem() -> bytes:
    """从 NAS 拉取当前固件的登录公钥。

    端点:`GET {NAS_BASE}/key/pub?signme=1` -> {"code":"200","data":{"key":"<PEM>"}}
    用 urllib 保持同步语义(auth 层被 async 客户端同步调用)。
    """
    import json
    import urllib.request

    base = os.environ.get("NAS_BASE") or ""
    if not base:
        host = os.environ.get("NAS_HOST", "").strip()
        if not host:
            raise RuntimeError("NAS_HOST / NAS_BASE 未设置,无法拉取公钥")
        base = f"http://{host}:5055"
    url = f"{base}/key/pub?signme=1"
    with urllib.request.urlopen(url, timeout=8) as resp:  # noqa: S310
        body = json.loads(resp.read().decode("utf-8"))
    if str(body.get("code")) != "200":
        raise RuntimeError(f"拉取公钥失败: {body.get('code')} {body.get('msg')}")
    return body["data"]["key"].encode("utf-8")


def _get_pubkey():
    """优先实时公钥 -> 磁盘缓存 -> 硬编码兜底。"""
    global _PUBKEY_RUNTIME
    if _PUBKEY_RUNTIME is not None:
        return _PUBKEY_RUNTIME
    try:
        pem = _fetch_pubkey_pem()
        _PUBKEY_RUNTIME = load_pem_public_key(pem)
        try:
            _PUBKEY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PUBKEY_CACHE_FILE.write_bytes(pem)
        except OSError:
            pass
        return _PUBKEY_RUNTIME
    except Exception:
        if _PUBKEY_CACHE_FILE.exists():
            try:
                _PUBKEY_RUNTIME = load_pem_public_key(
                    _PUBKEY_CACHE_FILE.read_bytes()
                )
                return _PUBKEY_RUNTIME
            except Exception:
                pass
        _PUBKEY_RUNTIME = _PUBKEY
        return _PUBKEY_RUNTIME


def encrypt_field(plain: str) -> str:
    """RSA-PKCS1v15 + base64. NAS /auth/login 要求."""
    cipher = _get_pubkey().encrypt(plain.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(cipher).decode("ascii")


def resolve_device_id() -> str:
    """获取 32 字符 device_id。

    优先级:
    1.  env `NAS_DEVICE_ID`(32 字符) — 用户明确指定
    2.  `NAS_DEVICE_ID` 非 32 字符 → 从机器指纹自动生成持久化值,
        存到 `~/.cache/zspace-mcp/device_id` 下次复用
    """
    did = os.environ.get("NAS_DEVICE_ID", "").strip()
    if len(did) == 32:
        return did

    # 自动生成:从 /etc/machine-id + NAS_HOST hash,32 字符 hex
    cache_dir = Path.home() / ".cache" / "zspace-mcp"
    cache_file = cache_dir / "device_id"
    if cache_file.exists():
        cached = cache_file.read_text().strip()
        if len(cached) == 32:
            return cached

    host = os.environ.get("NAS_HOST", "unknown")
    try:
        machine = Path("/etc/machine-id").read_text().strip()
    except Exception:
        try:
            machine = Path("/var/lib/dbus/machine-id").read_text().strip()
        except Exception:
            machine = os.uname().nodename
    seed = f"{machine}:{host}"
    did = hashlib.sha256(seed.encode()).hexdigest()[:32]

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(did)
    return did
