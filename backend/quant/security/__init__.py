"""安全层：密钥派生、字段加密、口令、JWT、TOTP、审计链。

这一层不依赖 Web 框架，可以单独用于 CLI / 批处理；API 层只是它的调用方。
"""

from .audit import GENESIS, AuditChain, AuditEntry, chain_hash, verify_chain
from .crypto import (
    INFO_AUDIT,
    INFO_FIELD,
    INFO_JWT,
    MasterKey,
    SecretBox,
    field_box,
    generate_key_b64,
    master_key,
    reset_caches,
)
from .password import (
    WeakPassword,
    hash_password,
    needs_rehash,
    validate_strength,
    verify_password,
)
from .password import (
    algorithm as password_algorithm,
)
from .tokens import ACCESS_TYPE, REFRESH_TYPE, TokenClaims, TokenPair, TokenService
from .totp import TotpService

__all__ = [
    "ACCESS_TYPE",
    "GENESIS",
    "INFO_AUDIT",
    "INFO_FIELD",
    "INFO_JWT",
    "REFRESH_TYPE",
    "AuditChain",
    "AuditEntry",
    "MasterKey",
    "SecretBox",
    "TokenClaims",
    "TokenPair",
    "TokenService",
    "TotpService",
    "WeakPassword",
    "chain_hash",
    "field_box",
    "generate_key_b64",
    "hash_password",
    "master_key",
    "needs_rehash",
    "password_algorithm",
    "reset_caches",
    "validate_strength",
    "verify_chain",
    "verify_password",
]
