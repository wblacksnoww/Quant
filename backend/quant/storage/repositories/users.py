"""用户仓储。"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import User, utcnow_naive

log = logging.getLogger(__name__)


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_id: int) -> User | None:
        return self.session.get(User, user_id)

    def get_by_username(self, username: str) -> User | None:
        return self.session.scalar(select(User).where(User.username == username))

    def count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(User)) or 0)

    def create(
        self,
        username: str,
        password_hash: str,
        *,
        role: str = "user",
        is_active: bool = True,
        approval_status: str = "approved",
    ) -> User:
        user = User(
            username=username.strip(),
            password_hash=password_hash,
            role=role,
            is_active=is_active,
            approval_status=approval_status,
        )
        self.session.add(user)
        self.session.flush()
        return user

    def list_all(self) -> list[User]:
        return list(self.session.scalars(select(User).order_by(User.id)))

    def update_password(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        user.updated_at = utcnow_naive()

    def register_success(self, user: User) -> None:
        user.failed_attempts = 0
        user.locked_until = None
        user.last_login_at = utcnow_naive()

    def register_failure(self, user: User, *, threshold: int = 5, lock_minutes: int = 15) -> None:
        user.failed_attempts = (user.failed_attempts or 0) + 1
        if user.failed_attempts >= threshold:
            user.locked_until = utcnow_naive() + timedelta(minutes=lock_minutes)
            log.warning(
                "账户 %s 连续失败 %d 次，锁定至 %s", user.username, user.failed_attempts, user.locked_until
            )

    def is_locked(self, user: User) -> bool:
        return bool(user.locked_until and user.locked_until > utcnow_naive())
