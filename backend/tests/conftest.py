"""测试夹具：隔离的临时数据目录、临时数据库与合成行情。"""

from __future__ import annotations

import base64
import secrets

import pytest
from fastapi.testclient import TestClient

from quant.config import Settings
from quant.security.crypto import reset_caches
from quant.server.app import create_app
from quant.server.context import build_context


@pytest.fixture()
def settings(tmp_path, monkeypatch) -> Settings:
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    monkeypatch.setenv("QUANT_MASTER_KEY", key)
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    reset_caches()
    return Settings(
        env="test",
        debug=True,
        database_url=f"sqlite:///{tmp_path}/test.db",
        data_dir=str(tmp_path),
        master_key=key,
        admin_username="admin",
        admin_password="Test!Passw0rd",
        data_provider="synthetic",
        fallback_providers=[],
        default_symbols=["600519", "000001"],
        allowed_origins=["*"],
        rate_limit_per_minute=10_000,
        max_concurrent_backtests=2,
    )


@pytest.fixture()
def context(settings: Settings):
    ctx = build_context(settings)
    try:
        yield ctx
    finally:
        try:
            ctx.paper.stop_all(timeout=5)
        finally:
            if ctx.backtests is not None:
                ctx.backtests.shutdown(timeout=5)
            ctx.db.dispose()
            reset_caches()


@pytest.fixture()
def client(settings: Settings, context):
    app = create_app(settings, context)
    with TestClient(app) as test_client:
        yield test_client


def login_headers(client: TestClient, password: str = "Test!Passw0rd") -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": "admin", "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
