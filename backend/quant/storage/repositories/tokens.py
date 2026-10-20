"""JWT refresh token 仓储。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, desc, select, update
from sqlalchemy.orm import Session

from ..models import RefreshToken, utcnow_naive


class TokenRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        user_id: int,
        jti: str,
        token_hash: str,
        expires_at: datetime,
        *,
        user_agent: str = "",
        ip: str = "",
    ) -> RefreshToken:
        record = RefreshToken(
            user_id=user_id,
            jti=jti,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent[:255],
            ip=ip[:64],
        )
        self.session.add(record)
        self.session.flush()
        return record

    def find(self, jti: str) -> RefreshToken | None:
        return self.session.scalar(select(RefreshToken).where(RefreshToken.jti == jti))

    def revoke(self, record: RefreshToken) -> None:
        record.revoked_at = utcnow_naive()

    def revoke_all(self, user_id: int) -> int:
        result = self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow_naive())
        )
        return int(result.rowcount or 0)

    def revoke_jti(self, jti: str) -> bool:
        record = self.find(jti)
        if record is None or record.revoked_at is not None:
            return False
        self.revoke(record)
        return True

    def purge_expired(self, *, days: int = 30) -> int:
        cutoff = utcnow_naive() - timedelta(days=days)
        result = self.session.execute(delete(RefreshToken).where(RefreshToken.expires_at < cutoff))
        return int(result.rowcount or 0)

    def active_sessions(self, user_id: int) -> list[RefreshToken]:
        return list(
            self.session.scalars(
                select(RefreshToken)
                .where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                    RefreshToken.expires_at > utcnow_naive(),
                )
                .order_by(desc(RefreshToken.created_at))
            )
        )
