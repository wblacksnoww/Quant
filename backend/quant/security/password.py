"""口令哈希：Argon2id。

为什么不用 SHA-256 / bcrypt：

* SHA-256 是通用哈希，GPU 每秒可算几十亿次，**绝对不能**用来存口令。
* Argon2id 是密码哈希竞赛（PHC）冠军，抗 GPU/ASIC，且被 OWASP 推荐为默认选择。
* 参数按 OWASP 2024 建议：``m=64MiB, t=3, p=4``，单次验证约 40~80ms，
  登录体验可接受，暴力破解成本极高。

若运行环境没有 ``argon2-cffi``，会退回到标准库的 ``scrypt``（仍然安全），
并在验证成功后自动升级为 Argon2id（前提是安装了依赖）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets

from ..core.errors import SecurityError

log = logging.getLogger(__name__)

_ARGON2_AVAILABLE = True
try:  # pragma: no cover - 依赖可选
    from argon2 import PasswordHasher
    from argon2.exceptions import InvalidHashError, VerifyMismatchError
    from argon2.exceptions import VerificationError
except ImportError:  # pragma: no cover
    _ARGON2_AVAILABLE = False
    PasswordHasher = None  # type: ignore[assignment]
    VerifyMismatchError = Exception  # type: ignore[assignment,misc]
    InvalidHashError = Exception  # type: ignore[assignment,misc]
    VerificationError = Exception  # type: ignore[assignment,misc]

_hasher = None
if _ARGON2_AVAILABLE:
    _hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)

MIN_LENGTH = 10


class WeakPassword(SecurityError):
    """口令强度不足。"""

    code = "weak_password"
    http_status = 422


def validate_strength(password: str) -> None:
    """最小强度校验：长度 + 字符类别。"""
    if len(password) < MIN_LENGTH:
        raise WeakPassword(f"密码至少 {MIN_LENGTH} 位")
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        raise WeakPassword("密码需包含大写、小写、数字、符号中的至少三类")


# --------------------------------------------------------------------------- #
# 主 API
# --------------------------------------------------------------------------- #


def hash_password(password: str) -> str:
    """生成口令哈希（Argon2id 优先）。"""
    if not password:
        raise WeakPassword("密码不能为空")
    if _ARGON2_AVAILABLE:
        return _hasher.hash(password)  # type: ignore[union-attr]
    return _scrypt_hash(password)


def verify_password(password: str, stored: str) -> bool:
    """常量时间校验口令。"""
    if not password or not stored:
        return False
    if stored.startswith("$argon2"):
        if not _ARGON2_AVAILABLE:
            log.error("检测到 Argon2 哈希但未安装 argon2-cffi，无法验证")
            return False
        try:
            return _hasher.verify(stored, password)  # type: ignore[union-attr]
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        except Exception:  # noqa: BLE001
            log.exception("口令验证异常")
            return False
    if stored.startswith("scrypt$"):
        return _scrypt_verify(password, stored)
    log.error("未知的口令哈希格式")
    return False


def needs_rehash(stored: str) -> bool:
    """是否需要升级哈希参数/算法。"""
    if stored.startswith("scrypt$"):
        return True
    if _ARGON2_AVAILABLE and stored.startswith("$argon2"):
        try:
            return _hasher.check_needs_rehash(stored)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            return True
    return False


# --------------------------------------------------------------------------- #
# scrypt 回退
# --------------------------------------------------------------------------- #

_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1


def _scrypt_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "scrypt${}${}${}".format(
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
        f"{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}",
    )


def _scrypt_verify(password: str, stored: str) -> bool:
    try:
        _, salt_b64, digest_b64, params = stored.split("$", 3)
        n, r, p = (int(x) for x in params.split(":"))
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(expected, actual)
    except Exception:  # noqa: BLE001
        return False


def algorithm() -> str:
    return "argon2id" if _ARGON2_AVAILABLE else "scrypt"


__all__ = [
    "MIN_LENGTH",
    "WeakPassword",
    "algorithm",
    "hash_password",
    "needs_rehash",
    "validate_strength",
    "verify_password",
]
