"""主密钥管理与字段级信封加密。

安全模型
========

1. **唯一根密钥（Master Key）**：32 字节随机数，只从环境变量 /
   Docker secret / 密钥文件读取，**绝不进数据库、不写日志**。
2. **子密钥派生**：用 HKDF-SHA256 从根密钥派生出彼此独立的子密钥——

   =====================  ==========================================
   用途                     info 标签
   =====================  ==========================================
   字段加密（券商密钥等）   ``quant/field-encryption``
   JWT 签名                ``quant/jwt-signing``
   审计链                  ``quant/audit``
   TOTP 种子加密           复用字段加密子密钥
   =====================  ==========================================

   这样即使某一个子密钥泄露，也不会波及另外两个用途。
3. **算法**：AES-256-GCM（认证加密）。密文被篡改会在解密时直接报错，
   而不是悄悄返回垃圾明文。
4. **AAD 绑定字段名**：把 ``credential.api_key`` 这类上下文作为附加认证数据，
   攻击者无法把 A 字段的密文挪到 B 字段上使用。
5. **密文格式**：``v1.<base64url(nonce || ciphertext || tag)>``，带版本号便于轮换算法。

根密钥的来源优先级见 :meth:`MasterKey.load`。开发环境允许自动生成并落盘，
**生产环境会拒绝自动生成**，避免"以为加密了，其实用的是临时密钥"。
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..core.errors import CryptoError, MasterKeyMissing

log = logging.getLogger(__name__)

KEY_BYTES = 32
NONCE_BYTES = 12
FORMAT_VERSION = "v1"
ENV_KEY = "QUANT_MASTER_KEY"
ENV_KEY_FILE = "QUANT_MASTER_KEY_FILE"
DOCKER_SECRET = "/run/secrets/quant_master_key"

# 子密钥标签
INFO_FIELD = b"quant/field-encryption"
INFO_JWT = b"quant/jwt-signing"
INFO_AUDIT = b"quant/audit"


# --------------------------------------------------------------------------- #
# 根密钥
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MasterKey:
    """32 字节根密钥及其来源（来源只用于日志，不含密钥内容）。"""

    raw: bytes
    source: str

    def __post_init__(self) -> None:
        if len(self.raw) != KEY_BYTES:
            raise MasterKeyMissing(f"主密钥长度必须为 {KEY_BYTES} 字节，实际 {len(self.raw)}")
        if len(set(self.raw)) == 1:
            raise MasterKeyMissing("主密钥看起来是全零/常量，拒绝使用")

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, *, allow_generate: bool | None = None, data_dir: str | Path = "data") -> MasterKey:
        """按优先级加载根密钥。

        顺序：环境变量 → 密钥文件路径（环境变量指定）→ Docker secret →
        本地开发文件（仅非生产环境，自动生成并告警）。
        """
        raw = _from_env()
        if raw:
            return cls(raw, "env:" + ENV_KEY)

        file_env = os.getenv(ENV_KEY_FILE)
        if file_env:
            return cls(_read_key_file(Path(file_env)), f"file:{file_env}")

        if Path(DOCKER_SECRET).exists():
            return cls(_read_key_file(Path(DOCKER_SECRET)), f"docker-secret:{DOCKER_SECRET}")

        if allow_generate is None:
            allow_generate = os.getenv("QUANT_ENV", "development").lower() not in ("production", "prod")

        dev_file = Path(data_dir) / "master.key"
        if not dev_file.is_absolute():
            dev_file = Path(__file__).resolve().parents[2] / dev_file

        if dev_file.exists():
            return cls(_read_key_file(dev_file), f"dev-file:{dev_file}")

        if not allow_generate:
            raise MasterKeyMissing(
                "生产环境必须提供主密钥：设置 QUANT_MASTER_KEY（base64）"
                "或 QUANT_MASTER_KEY_FILE，或挂载 /run/secrets/quant_master_key"
            )

        key = secrets.token_bytes(KEY_BYTES)
        dev_file.parent.mkdir(parents=True, exist_ok=True)
        dev_file.write_text(base64.urlsafe_b64encode(key).decode("ascii"), encoding="utf-8")
        try:
            os.chmod(dev_file, 0o600)
        except OSError:  # pragma: no cover - Windows 等平台
            pass
        log.warning(
            "未检测到主密钥，已在 %s 生成开发用密钥（权限 600）。"
            "生产环境请改用环境变量或 Docker secret，并妥善备份——密钥丢失则历史密文无法恢复。",
            dev_file,
        )
        return cls(key, f"generated:{dev_file}")

    # ------------------------------------------------------------------ #
    def derive(self, info: bytes, length: int = 32) -> bytes:
        """HKDF-SHA256 派生子密钥。"""
        return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(self.raw)

    @property
    def fingerprint(self) -> str:
        """密钥指纹（SHA-256 前 8 字节 hex），用于确认多实例是否共用同一密钥。"""
        import hashlib

        return hashlib.sha256(self.raw).hexdigest()[:16]

    def to_b64(self) -> str:
        """导出 base64（备份用，慎用；不要打印到日志）。"""
        return base64.urlsafe_b64encode(self.raw).decode("ascii")


def _from_env() -> bytes | None:
    value = os.getenv(ENV_KEY)
    if not value:
        return None
    return _decode_key(value.strip())


def _decode_key(text: str) -> bytes:
    """支持 base64（标准/URL）与 hex 两种写法。"""
    text = text.strip()
    if len(text) == KEY_BYTES * 2:
        try:
            return bytes.fromhex(text)
        except ValueError:
            pass
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            raw = decoder(text + "=" * (-len(text) % 4))
            if len(raw) == KEY_BYTES:
                return raw
        except (binascii.Error, ValueError):
            continue
    raise MasterKeyMissing("主密钥格式无法识别：应为 32 字节的 base64 或 64 位 hex 字符串")


def _read_key_file(path: Path) -> bytes:
    if not path.exists():
        raise MasterKeyMissing(f"主密钥文件不存在: {path}")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise MasterKeyMissing(f"主密钥文件为空: {path}")
    return _decode_key(content)


# --------------------------------------------------------------------------- #
# 字段级加密
# --------------------------------------------------------------------------- #


class SecretBox:
    """AES-256-GCM 字段加密器。

    一个实例只持有一个子密钥；不同用途请用不同的 :class:`SecretBox`。
    """

    def __init__(self, key: bytes, *, name: str = "field") -> None:
        if len(key) != KEY_BYTES:
            raise CryptoError(f"{name} 子密钥长度错误")
        self._aead = AESGCM(key)
        self.name = name

    @classmethod
    def from_master(cls, master: MasterKey, info: bytes = INFO_FIELD, name: str = "field") -> SecretBox:
        return cls(master.derive(info), name=name)

    # ------------------------------------------------------------------ #
    def encrypt(self, plaintext: str | bytes, *, aad: str | bytes | None = None) -> str:
        """加密为 ``v1.<base64url>`` 字符串。"""
        data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
        nonce = secrets.token_bytes(NONCE_BYTES)
        blob = self._aead.encrypt(nonce, data, _aad(aad))
        token = base64.urlsafe_b64encode(nonce + blob).decode("ascii").rstrip("=")
        return f"{FORMAT_VERSION}.{token}"

    def decrypt(self, token: str, *, aad: str | bytes | None = None) -> str:
        """解密。密文被篡改或 AAD 不匹配会抛 :class:`CryptoError`。"""
        if not token:
            raise CryptoError("密文为空")
        try:
            version, _, payload = token.partition(".")
            if version != FORMAT_VERSION:
                raise CryptoError(f"不支持的密文版本: {version!r}")
            raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            nonce, blob = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
            return self._aead.decrypt(nonce, blob, _aad(aad)).decode("utf-8")
        except InvalidTag as exc:
            raise CryptoError("解密失败：密文被篡改或主密钥不匹配") from exc
        except (binascii.Error, ValueError) as exc:
            raise CryptoError(f"密文格式非法: {exc}") from exc

    # ------------------------------------------------------------------ #
    def encrypt_json(self, obj: Mapping[str, Any], *, aad: str | bytes | None = None) -> str:
        return self.encrypt(json.dumps(obj, ensure_ascii=False, sort_keys=True), aad=aad)

    def decrypt_json(self, token: str, *, aad: str | bytes | None = None) -> dict[str, Any]:
        return json.loads(self.decrypt(token, aad=aad))

    def mask(self, token: str, *, keep: int = 4) -> str:
        """把密文转成可展示的掩码（只显示尾部若干字符）。"""
        return "•" * 8 + token[-keep:] if len(token) > keep else "•" * 8


def _aad(value: str | bytes | None) -> bytes | None:
    if value is None:
        return None
    return value.encode("utf-8") if isinstance(value, str) else value


# --------------------------------------------------------------------------- #
# 进程级单例
# --------------------------------------------------------------------------- #

_master: MasterKey | None = None
_field_box: SecretBox | None = None


def master_key() -> MasterKey:
    global _master
    if _master is None:
        _master = MasterKey.load(data_dir=os.getenv("QUANT_DATA_DIR", "data"))
    return _master


def field_box() -> SecretBox:
    global _field_box
    if _field_box is None:
        _field_box = SecretBox.from_master(master_key(), INFO_FIELD, name="field")
    return _field_box


def reset_caches() -> None:
    """测试用：清空单例，便于切换密钥。"""
    global _master, _field_box
    _master = None
    _field_box = None


def generate_key_b64() -> str:
    """生成一个新的根密钥（CLI / 部署脚本用）。"""
    return base64.urlsafe_b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii")


__all__ = [
    "ENV_KEY",
    "ENV_KEY_FILE",
    "FORMAT_VERSION",
    "INFO_AUDIT",
    "INFO_FIELD",
    "INFO_JWT",
    "KEY_BYTES",
    "MasterKey",
    "SecretBox",
    "field_box",
    "generate_key_b64",
    "master_key",
    "reset_caches",
]
