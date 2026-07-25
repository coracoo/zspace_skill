"""NAS 登录加密层(RSA-PKCS1v15 + base64)。

4 处复用:dashboard/app.py、zspace/mcp_server.py、.claude/skills/*/lib/。
"""
import base64
import os

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
    """优先 env NAS_DEVICE_ID,否则用代码默认值。始终 32 字符。"""
    did = os.environ.get("NAS_DEVICE_ID", "").strip()
    return did if (len(did) == 32) else NAS_DEVICE_ID_DEFAULT
