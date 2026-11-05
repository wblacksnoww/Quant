"""CLI 用户管理：passwd 是忘记密码时唯一的自救通道，行为要锁死。"""

from __future__ import annotations

import base64
import secrets
from datetime import timedelta

import pytest
from sqlalchemy import select

from quant import cli
from quant.config import Settings, reset_settings
from quant.security.password import verify_password
from quant.storage.db import Database
from quant.storage.models import RefreshToken, utcnow_naive
from quant.storage.repositories import TokenRepository, UserRepository


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把 CLI 指向临时 sqlite 库，避免碰开发库。"""
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    monkeypatch.setenv("QUANT_MASTER_KEY", key)
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("QUANT_DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    # get_settings 带 lru_cache：不清掉的话第二个用例还在用第一个的库
    reset_settings()
    yield Settings(
        env="test",
        database_url=f"sqlite:///{tmp_path}/cli.db",
        data_dir=str(tmp_path),
        master_key=key,
    )
    reset_settings()


def _with_session(url: str):
    db = Database(url)
    return db


def test_passwd_rewrites_hash_and_clears_lock(env):
    assert cli.main(["user", "create", "--username", "bob", "--password", "Abcdefgh12!"]) == 0

    db = _with_session(env.database_url)
    with db.session() as session:
        repo = UserRepository(session)
        user = repo.get_by_username("bob")
        assert user is not None
        # 制造一个"连续失败五次已锁定"的现场
        for _ in range(5):
            repo.register_failure(user, threshold=5, lock_minutes=15)
        assert repo.is_locked(user)
    db.dispose()

    assert cli.main(["user", "passwd", "--username", "bob", "--password", "NewSecret123!"]) == 0

    db = _with_session(env.database_url)
    with db.session() as session:
        user = UserRepository(session).get_by_username("bob")
        assert verify_password("NewSecret123!", user.password_hash)
        assert not verify_password("Abcdefgh12!", user.password_hash)
        assert user.failed_attempts == 0
        assert user.locked_until is None
    db.dispose()


def test_passwd_revokes_existing_refresh_tokens(env):
    cli.main(["user", "create", "--username", "bob", "--password", "Abcdefgh12!"])
    expires = utcnow_naive() + timedelta(days=7)

    db = _with_session(env.database_url)
    with db.session() as session:
        user = UserRepository(session).get_by_username("bob")
        tokens = TokenRepository(session)
        tokens.create(user.id, jti="jti-1", token_hash="h1", expires_at=expires)
        tokens.create(user.id, jti="jti-2", token_hash="h2", expires_at=expires)
    db.dispose()

    assert cli.main(["user", "passwd", "--username", "bob", "--password", "NewSecret123!"]) == 0

    db = _with_session(env.database_url)
    with db.session() as session:
        records = list(session.scalars(select(RefreshToken).order_by(RefreshToken.id)))
        assert len(records) == 2
        # 改密后旧会话必须失效，否则丢了密码等于没丢
        assert all(record.revoked_at is not None for record in records)
    db.dispose()


def test_passwd_keep_lock_flag(env):
    cli.main(["user", "create", "--username", "bob", "--password", "Abcdefgh12!"])

    db = _with_session(env.database_url)
    with db.session() as session:
        repo = UserRepository(session)
        user = repo.get_by_username("bob")
        for _ in range(5):
            repo.register_failure(user, threshold=5, lock_minutes=15)
    db.dispose()

    assert (
        cli.main(["user", "passwd", "--username", "bob", "--password", "NewSecret123!", "--keep-lock"]) == 0
    )

    db = _with_session(env.database_url)
    with db.session() as session:
        user = UserRepository(session).get_by_username("bob")
        assert user.failed_attempts == 5
        assert user.locked_until is not None
    db.dispose()


def test_passwd_unknown_user_fails_without_creating(env):
    assert cli.main(["user", "passwd", "--username", "ghost", "--password", "NewSecret123!"]) == 1

    db = _with_session(env.database_url)
    with db.session() as session:
        assert UserRepository(session).get_by_username("ghost") is None
    db.dispose()


def test_passwd_rejects_weak_password(env):
    cli.main(["user", "create", "--username", "bob", "--password", "Abcdefgh12!"])
    assert cli.main(["user", "passwd", "--username", "bob", "--password", "abc"]) == 2

    db = _with_session(env.database_url)
    with db.session() as session:
        user = UserRepository(session).get_by_username("bob")
        assert verify_password("Abcdefgh12!", user.password_hash)
    db.dispose()


def test_create_still_rejects_duplicate(env):
    assert cli.main(["user", "create", "--username", "bob", "--password", "Abcdefgh12!"]) == 0
    assert cli.main(["user", "create", "--username", "bob", "--password", "Abcdefgh12!"]) == 1
