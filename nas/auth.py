"""NAS 登录加密层(RSA-PKCS1v15 + base64)。

4 处复用:dashboard/app.py、zspace/mcp_server.py、skills/*/lib/。
"""
import base64
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

# 注:这是 NAS 公开端点 /zspace/system/private/pubkey 返回的 RSA 公钥,
# 用于登录加密,非私钥/密码。更换 NAS 固件版本时可能需要更新。
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


def encrypt_field(plain: str) -> str:
    """RSA-PKCS1v15 + base64. NAS /auth/login 要求."""
    cipher = _PUBKEY.encrypt(plain.encode("utf-8"), padding.PKCS1v15())
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
