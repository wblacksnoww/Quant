"""拼装策略共享：完全共享、点对点、fork、版本同步与管理员管理。"""

from __future__ import annotations

from conftest import login_headers

PASSWORD = "Str0ng!Passw0rd"
SPEC = {
    "version": 1,
    "description": "恒买",
    "category": "trend",
    "entry": [
        {
            "type": "gt",
            "left": {"kind": "const", "value": 1},
            "right": {"kind": "const", "value": 0},
        }
    ],
    "exit": [
        {
            "type": "lt",
            "left": {"kind": "const", "value": 1},
            "right": {"kind": "const", "value": 0},
        }
    ],
    "position_pct": 0.5,
    "stop_loss": 0.0,
    "take_profit": 0.0,
}


def _user_headers(client, username: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_user(client, admin_headers: dict[str, str], username: str):
    resp = client.post(
        "/api/admin/users",
        json={"username": username, "password": PASSWORD, "role": "user"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text


def test_public_share_fork_and_manual_sync(client):
    admin = login_headers(client)
    _make_user(client, admin, "sharer")
    _make_user(client, admin, "receiver")
    owner = _user_headers(client, "sharer")
    receiver = _user_headers(client, "receiver")

    created = client.post(
        "/api/strategies/custom",
        json={"name": "共享趋势", "description": "v1", "spec": SPEC},
        headers=owner,
    )
    assert created.status_code == 201, created.text
    strategy = created.json()
    sid = strategy["id"]
    assert strategy["version"] == 1

    shared = client.post(
        f"/api/strategies/custom/{sid}/shares",
        json={"scope": "public"},
        headers=owner,
    )
    assert shared.status_code == 201, shared.text
    assert shared.json()["scope"] == "public"

    listing = client.get("/api/strategies/shared", headers=receiver)
    assert listing.status_code == 200, listing.text
    item = next(row for row in listing.json() if row["strategy_id"] == sid)
    assert item["share_type"] == "public"
    assert item["owner_username"] == "sharer"
    assert item["fork_id"] is None

    forked = client.post(f"/api/strategies/shared/{sid}/fork", headers=receiver)
    assert forked.status_code == 201, forked.text
    fork = forked.json()
    assert fork["source_strategy_id"] == sid
    assert fork["source_version"] == 1
    assert fork["version"] == 1

    # 源策略更新 → 副本提示有更新，但不会自动改
    updated_spec = {**SPEC, "description": "v2", "position_pct": 0.8}
    updated = client.patch(
        f"/api/strategies/custom/{sid}",
        json={"spec": updated_spec},
        headers=owner,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2

    mine = client.get("/api/strategies/custom", headers=receiver).json()
    fork_row = next(row for row in mine if row["id"] == fork["id"])
    assert fork_row["update_available"] is True
    assert fork_row["source_version"] == 1

    synced = client.post(f"/api/strategies/custom/{fork['id']}/sync", headers=receiver)
    assert synced.status_code == 200, synced.text
    assert synced.json()["source_version"] == 2
    assert synced.json()["version"] == 2
    assert synced.json()["spec"]["position_pct"] == 0.8
    assert synced.json()["update_available"] is False


def test_point_to_point_share_visibility_and_revoke(client):
    admin = login_headers(client)
    _make_user(client, admin, "p2p-owner")
    _make_user(client, admin, "p2p-target")
    _make_user(client, admin, "p2p-other")
    owner = _user_headers(client, "p2p-owner")
    target = _user_headers(client, "p2p-target")
    other = _user_headers(client, "p2p-other")

    created = client.post(
        "/api/strategies/custom",
        json={"name": "点对点策略", "spec": SPEC},
        headers=owner,
    ).json()
    sid = created["id"]
    resp = client.post(
        f"/api/strategies/custom/{sid}/shares",
        json={"scope": "user", "target_username": "p2p-target"},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    share_id = resp.json()["id"]

    visible = client.get("/api/strategies/shared", headers=target).json()
    assert any(row["strategy_id"] == sid and row["share_type"] == "user" for row in visible)
    assert all(
        row["strategy_id"] != sid for row in client.get("/api/strategies/shared", headers=other).json()
    )

    shares = client.get(f"/api/strategies/custom/{sid}/shares", headers=owner).json()
    assert shares[0]["target_username"] == "p2p-target"

    revoked = client.delete(f"/api/strategies/custom/{sid}/shares/{share_id}", headers=owner)
    assert revoked.status_code == 200
    assert all(
        row["strategy_id"] != sid for row in client.get("/api/strategies/shared", headers=target).json()
    )


def test_admin_can_list_disable_and_delete_strategies(client):
    admin = login_headers(client)
    _make_user(client, admin, "admin-strategy-owner")
    owner = _user_headers(client, "admin-strategy-owner")
    created = client.post(
        "/api/strategies/custom",
        json={"name": "管理员可管策略", "spec": SPEC},
        headers=owner,
    ).json()
    sid = created["id"]

    listing = client.get("/api/admin/strategies", headers=admin)
    assert listing.status_code == 200, listing.text
    row = next(item for item in listing.json() if item["id"] == sid)
    assert row["owner_username"] == "admin-strategy-owner"

    disabled = client.patch(f"/api/admin/strategies/{sid}", json={"enabled": False}, headers=admin)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False

    deleted = client.delete(f"/api/admin/strategies/{sid}", headers=admin)
    assert deleted.status_code == 200
    assert client.get("/api/admin/strategies", headers=admin).status_code == 200
