"""注册审核、人机验证与用户反馈。"""

from __future__ import annotations

import time

from conftest import login_headers

from quant.services import captcha_service

PASSWORD = "Str0ng!Passw0rd"


def _register(client, context, username: str, password: str = PASSWORD):
    challenge = captcha_service.issue(context.master.raw)
    return client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": password,
            "captcha_token": challenge.token,
            "captcha_answer": challenge.answer,
        },
    )


def _login(client, username: str, password: str = PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_register_requires_captcha_and_admin_approval(client, context):
    bad = client.post(
        "/api/auth/register",
        json={
            "username": "pending-user",
            "password": PASSWORD,
            "captcha_token": "not-a-token",
            "captcha_answer": "XXXX",
        },
    )
    assert bad.status_code == 422

    resp = _register(client, context, "pending-user")
    assert resp.status_code == 201, resp.text
    assert "等待管理员审核" in resp.json()["message"]

    # 待审核账号不能登录
    login = _login(client, "pending-user")
    assert login.status_code == 403
    assert "审核" in login.json()["message"]

    headers = login_headers(client)
    users = client.get("/api/admin/users", headers=headers).json()
    user = next(item for item in users if item["username"] == "pending-user")
    assert user["approval_status"] == "pending"
    assert user["is_active"] is False

    approved = client.patch(
        f"/api/admin/users/{user['id']}/approval",
        json={"action": "approve"},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status"] == "approved"

    assert _login(client, "pending-user").status_code == 200


def test_rejected_registration_cannot_login(client, context):
    assert _register(client, context, "rejected-user").status_code == 201
    headers = login_headers(client)
    user = next(
        item
        for item in client.get("/api/admin/users", headers=headers).json()
        if item["username"] == "rejected-user"
    )
    resp = client.patch(
        f"/api/admin/users/{user['id']}/approval",
        json={"action": "reject"},
        headers=headers,
    )
    assert resp.status_code == 200
    login = _login(client, "rejected-user")
    assert login.status_code == 403
    assert "未通过" in login.json()["message"]


def test_feedback_only_visible_to_admin(client, context):
    assert _register(client, context, "feedback-user").status_code == 201
    headers = login_headers(client)
    user = next(
        item
        for item in client.get("/api/admin/users", headers=headers).json()
        if item["username"] == "feedback-user"
    )
    client.patch(f"/api/admin/users/{user['id']}/approval", json={"action": "approve"}, headers=headers)
    login = _login(client, "feedback-user")
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = client.post(
        "/api/feedback",
        json={"category": "suggestion", "content": "希望增加暗色/亮色主题切换"},
        headers=user_headers,
    )
    assert resp.status_code == 201, resp.text
    feedback_id = resp.json()["detail"]["id"]

    forbidden = client.get("/api/admin/feedback", headers=user_headers)
    assert forbidden.status_code == 403

    listing = client.get("/api/admin/feedback", headers=headers)
    assert listing.status_code == 200
    row = next(item for item in listing.json() if item["id"] == feedback_id)
    assert row["username"] == "feedback-user"
    assert row["status"] == "new"

    updated = client.patch(
        f"/api/admin/feedback/{feedback_id}",
        json={"status": "resolved"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "resolved"


def test_captcha_token_expires_and_rejects_wrong_answer(context):
    challenge = captcha_service.issue(context.master.raw, ttl=30)
    assert captcha_service.verify(context.master.raw, challenge.token, challenge.answer)
    assert not captcha_service.verify(context.master.raw, challenge.token, "ZZZZ")
    assert not captcha_service.verify(
        context.master.raw,
        challenge.token,
        challenge.answer,
        now=int(time.time()) + 3600,
    )
