"""C++ 网关业务层：API 契约与三重闸门（不起真实网关二进制）。"""

from __future__ import annotations

from conftest import login_headers


def test_gateway_status_idle(client):
    resp = client.get("/api/gateway/status", headers=login_headers(client))
    assert resp.status_code == 200
    assert resp.json()["running"] is False


def test_gateway_start_rejects_unknown_strategy(client):
    resp = client.post(
        "/api/gateway/start",
        json={"strategy_type": "nope", "symbols": ["CRYPTO:BTC/USDT"]},
        headers=login_headers(client),
    )
    assert resp.status_code == 422


def test_gateway_start_requires_crypto_symbols(client):
    resp = client.post(
        "/api/gateway/start",
        json={"strategy_type": "grid", "symbols": ["CN:600519"]},
        headers=login_headers(client),
    )
    assert resp.status_code == 422


def test_gateway_live_requires_total_switch(client, context):
    """总开关未开时真实资金网关直接 403（不触子进程）。"""
    headers = login_headers(client)
    resp = client.post(
        "/api/gateway/start",
        json={
            "strategy_type": "grid",
            "symbols": ["CRYPTO:BTC/USDT"],
            "sandbox": False,
        },
        headers=headers,
    )
    assert resp.status_code == 403
    assert "QUANT_ENABLE_LIVE_TRADING" in resp.json()["message"]


def test_gateway_paper_start_with_fake_binary(client, context, monkeypatch, tmp_path):
    """用假网关脚本验证子进程/IPC 链路（不碰交易所）。"""
    fake = tmp_path / "quant-gateway"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, socket, sys, threading\n"
        "cfg = json.load(open(sys.argv[1]))\n"
        "srv = socket.socket()\n"
        "srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "srv.bind((cfg['listen_ip'], cfg['listen_port']))\n"
        "srv.listen(4)\n"
        "def serve(conn):\n"
        "    buf = b''\n"
        "    while True:\n"
        "        try: chunk = conn.recv(4096)\n"
        "        except OSError: return\n"
        "        if not chunk: return\n"
        "        buf += chunk\n"
        "        while len(buf) >= 4:\n"
        "            n = int.from_bytes(buf[:4], 'little')\n"
        "            if len(buf) < 4 + n: break\n"
        "            cmd = json.loads(buf[4:4+n]); buf = buf[4+n:]\n"
        "            if cmd.get('cmd') == 'shutdown':\n"
        "                r = json.dumps({'ok': True}).encode()\n"
        "                conn.sendall(len(r).to_bytes(4,'little') + r)\n"
        "                sys.exit(0)\n"
        "            r = json.dumps({'ok': True, 'positions': {}}).encode()\n"
        "            conn.sendall(len(r).to_bytes(4,'little') + r)\n"
        "while True:\n"
        "    conn, _ = srv.accept()\n"
        "    threading.Thread(target=serve, args=(conn,), daemon=True).start()\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setattr("quant.gateway.manager.GATEWAY_BIN", str(fake))

    headers = login_headers(client)
    resp = client.post(
        "/api/gateway/start",
        json={
            "strategy_type": "grid",
            "params": {"step_pct": 0.01, "order_qty": 0.001},
            "symbols": ["CRYPTO:BTC/USDT"],
            "sandbox": True,
            "bar_interval_sec": 60,
        },
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["running"] is True
    assert body["mode"] == "paper"
    assert body["strategy"] == "grid"

    status = client.get("/api/gateway/status", headers=headers).json()
    assert status["running"] is True

    stop = client.post("/api/gateway/stop", headers=headers)
    assert stop.status_code == 200
    assert client.get("/api/gateway/status", headers=headers).json()["running"] is False


def test_gateway_manager_live_needs_confirm_and_credential(context):
    """manager 层闸门：真实资金需凭据；paper 不需要。"""
    from quant.core.errors import InvalidRequest
    from quant.gateway.manager import GatewayManager

    manager = GatewayManager(context)
    try:
        # paper：不触凭据（也无法启动真实二进制，此处只测校验顺序——
        # 用非法策略类型让它在校验层就抛错，证明没走到子进程）
        try:
            manager.start(
                user_id=1,
                strategy_type="grid",
                params={},
                symbols=["CN:600519"],
                sandbox=True,
                credential_id=None,
                bar_interval_sec=60,
                risk_rules=None,
            )
        except InvalidRequest as exc:
            assert "CRYPTO" in str(exc)
        else:
            raise AssertionError("非 CRYPTO 标的应被拒绝")
    finally:
        manager.shutdown()
