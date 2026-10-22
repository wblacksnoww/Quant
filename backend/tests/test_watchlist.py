"""账号级自选：覆盖保存、规范化与用户隔离。"""

from __future__ import annotations

from conftest import login_headers

from quant.storage.repositories.watchlist import WatchlistRepository


def test_watchlist_replace_and_read(client):
    headers = login_headers(client)
    resp = client.put(
        "/api/watchlist",
        json={"symbols": ["600519", "CRYPTO:BTC/USDT", "600519", "AAPL"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == ["CN:600519", "CRYPTO:BTC/USDT", "US:AAPL"]

    listing = client.get("/api/watchlist", headers=headers)
    assert listing.status_code == 200
    assert listing.json() == ["CN:600519", "CRYPTO:BTC/USDT", "US:AAPL"]

    # 顺序保留
    resp = client.put(
        "/api/watchlist",
        json={"symbols": ["US:AAPL", "CN:600519"]},
        headers=headers,
    )
    assert resp.json() == ["US:AAPL", "CN:600519"]


def test_watchlist_limit_and_user_isolation(client, context):
    headers = login_headers(client)
    too_many = [f"CN:{index:06d}" for index in range(1, 32)]
    resp = client.put("/api/watchlist", json={"symbols": too_many}, headers=headers)
    assert resp.status_code == 422

    with context.db.session() as session:
        from quant.storage.repositories import UserRepository

        other = UserRepository(session).create("watchlist-other", "x", role="user")
        repo = WatchlistRepository(session)
        repo.replace(1, ["CN:600519"])
        repo.replace(other.id, ["US:AAPL"])
        assert repo.list_for(1) == ["CN:600519"]
        assert repo.list_for(other.id) == ["US:AAPL"]
