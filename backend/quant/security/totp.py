"""TOTP 两步验证（Google Authenticator / 1Password / 微软验证器通用）。

实现遵循 RFC 6238：

* 30 秒时间步长、6 位数字、SHA-1 —— 兼容主流验证器 App。
* 允许 ±1 个时间步的时钟漂移（``valid_window=1``），避免因为几秒钟的
  时间差把用户锁在门外。
* 种子（secret）以 AES-GCM **加密后**入库，数据库泄露也无法直接生成验证码。
* 提供一次性恢复码，防止手机丢失后永久失去账户。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from collections.abc import Iterable

import pyotp

from .crypto import SecretBox

log = logging.getLogger(__name__)

DIGEST = hashlib.sha1
DIGITS = 6
INTERVAL = 30
WINDOW = 1


class TotpService:
    """TOTP 生成/校验。种子加解密依赖 :class:`SecretBox`。"""

    def __init__(self, box: SecretBox, issuer: str = "QuantTrading") -> None:
        self.box = box
        self.issuer = issuer

    # ------------------------------------------------------------------ #
    @staticmethod
    def generate_secret() -> str:
        """生成新的 base32 种子。"""
        return pyotp.random_base32()

    def encrypt_secret(self, secret: str, *, user_id: int | str) -> str:
        """加密种子，AAD 绑定用户 ID，防止把 A 的种子挪给 B 用。"""
        return self.box.encrypt(secret, aad=f"totp:{user_id}")

    def decrypt_secret(self, token: str, *, user_id: int | str) -> str:
        return self.box.decrypt(token, aad=f"totp:{user_id}")

    # ------------------------------------------------------------------ #
    def provisioning_uri(self, secret: str, account: str) -> str:
        """生成 otpauth:// URI，前端渲染成二维码即可扫码绑定。"""
        totp = pyotp.TOTP(secret, digits=DIGITS, interval=INTERVAL, digest=DIGEST)
        return totp.provisioning_uri(name=account, issuer_name=self.issuer)

    def current_code(self, secret: str) -> str:
        return pyotp.TOTP(secret, digits=DIGITS, interval=INTERVAL, digest=DIGEST).now()

    def verify(self, secret: str, code: str) -> bool:
        """校验 6 位验证码（允许 ±1 个时间步）。"""
        code = (code or "").strip().replace(" ", "")
        if not code.isdigit() or len(code) != DIGITS:
            return False
        totp = pyotp.TOTP(secret, digits=DIGITS, interval=INTERVAL, digest=DIGEST)
        return bool(totp.verify(code, valid_window=WINDOW))

    # ------------------------------------------------------------------ #
    @staticmethod
    def generate_recovery_codes(count: int = 8) -> list[str]:
        """生成一次性恢复码（形如 ``A1B2-C3D4``）。"""
        codes = []
        for _ in range(count):
            raw = secrets.token_hex(4).upper()
            codes.append(f"{raw[:4]}-{raw[4:]}")
        return codes

    @staticmethod
    def hash_recovery_code(code: str) -> str:
        """恢复码入库前先规范化再哈希。"""
        normalized = code.strip().upper().replace("-", "").replace(" ", "")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @classmethod
    def verify_recovery_code(cls, code: str, hashed: Iterable[str]) -> bool:
        target = cls.hash_recovery_code(code)
        return any(hmac.compare_digest(target, h) for h in hashed)


__all__ = ["DIGEST", "DIGITS", "INTERVAL", "TotpService"]
