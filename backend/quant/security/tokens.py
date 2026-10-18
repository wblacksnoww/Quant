"""JWT 令牌：短期 access + 可轮换 refresh。

设计要点：

* **签名密钥**由主密钥 HKDF 派生（``quant/jwt-signing``），不是随机字符串，
  因此多实例部署只需共享主密钥即可互认令牌。
* access token 有效期短（默认 15 分钟），refresh token 长（默认 7 天）且
  **一次性**：每次刷新都签发新的 refresh，旧的立刻作废（令牌轮换），
  可有效降低 refresh 被盗用的窗口。
* 数据库里只存 refresh 的 **SHA-256 摘要**，不存原文——即使库被拖走也无法直接使用。
* ``jti`` 唯一标识一枚令牌，便于精确吊销。
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from ..core.errors import AuthError
from .crypto import INFO_JWT, MasterKey

ALGORITHM = "HS256"
ISSUER = "quant-trading"
ACCESS_TYPE = "access"
REFRESH_TYPE = "refresh"


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900
    refresh_expires_in: int = 604800


@dataclass(frozen=True)
class TokenClaims:
    sub: str
    role: str
    type: str
    jti: str
    issued_at: datetime
    expires_at: datetime
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def user_id(self) -> int:
        try:
            return int(self.sub)
        except (TypeError, ValueError) as exc:
            raise AuthError("令牌中的用户标识非法") from exc


class TokenService:
    """签发与校验 JWT。"""

    def __init__(
        self,
        master: MasterKey,
        *,
        access_ttl: int = 900,
        refresh_ttl: int = 7 * 24 * 3600,
        issuer: str = ISSUER,
    ) -> None:
        self._key = master.derive(INFO_JWT)
        self.access_ttl = access_ttl
        self.refresh_ttl = refresh_ttl
        self.issuer = issuer

    # ------------------------------------------------------------------ #
    def issue_pair(self, user_id: int | str, role: str = "user", **extra: Any) -> TokenPair:
        access = self.issue_access(user_id, role, **extra)
        refresh, _ = self.issue_refresh(user_id, role)
        return TokenPair(
            access_token=access,
            refresh_token=refresh,
            expires_in=self.access_ttl,
            refresh_expires_in=self.refresh_ttl,
        )

    def issue_access(self, user_id: int | str, role: str = "user", **extra: Any) -> str:
        return self._encode(str(user_id), role, ACCESS_TYPE, self.access_ttl, extra)

    def issue_refresh(self, user_id: int | str, role: str = "user") -> tuple[str, str]:
        """返回 ``(token, jti)``，jti 用于在库中登记/吊销。"""
        jti = secrets.token_urlsafe(18)
        token = self._encode(str(user_id), role, REFRESH_TYPE, self.refresh_ttl, {}, jti=jti)
        return token, jti

    # ------------------------------------------------------------------ #
    def _encode(
        self,
        sub: str,
        role: str,
        token_type: str,
        ttl: int,
        extra: dict[str, Any],
        *,
        jti: str | None = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "sub": sub,
            "role": role,
            "type": token_type,
            "iss": self.issuer,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=ttl)).timestamp()),
            "jti": jti or secrets.token_urlsafe(18),
        }
        if extra:
            payload["extra"] = extra
        return jwt.encode(payload, self._key, algorithm=ALGORITHM)

    # ------------------------------------------------------------------ #
    def decode(self, token: str, *, expected_type: str | None = None) -> TokenClaims:
        """解码并校验签名/过期/类型。"""
        if not token:
            raise AuthError("缺少令牌")
        try:
            payload = jwt.decode(
                token,
                self._key,
                algorithms=[ALGORITHM],
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "jti"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("令牌已过期") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthError("令牌签发者不匹配") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError(f"令牌无效: {exc}") from exc

        token_type = str(payload.get("type", ""))
        if expected_type and token_type != expected_type:
            raise AuthError(f"令牌类型错误：需要 {expected_type}")

        return TokenClaims(
            sub=str(payload["sub"]),
            role=str(payload.get("role", "user")),
            type=token_type,
            jti=str(payload["jti"]),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=timezone.utc),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
            extra=dict(payload.get("extra") or {}),
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def hash_token(token: str) -> str:
        """refresh token 的入库摘要（SHA-256）。"""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def extract_bearer(header_value: str | None) -> str | None:
        if not header_value:
            return None
        parts = header_value.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return None


__all__ = [
    "ACCESS_TYPE",
    "ALGORITHM",
    "ISSUER",
    "REFRESH_TYPE",
    "TokenClaims",
    "TokenPair",
    "TokenService",
]
