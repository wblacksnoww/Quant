"""GatewayManager：C++ 网关子进程的业务层管理。

职责边界：

* **网关（C++）**：OKX WS 连接、tick 聚合、策略、风控、下单（策略层）；
* **本管理器（Python）**：启动/停止子进程、生成配置（策略参数 + 风控规则 + 密钥 env）、
  TCP 事件接入（→ EventBus/审计/前端）、状态查询（业务层）。

网关只绑 127.0.0.1；密钥经环境变量传递不落盘；paper 模式订单不进交易所。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from typing import Any

from ..core.errors import ConflictError, InvalidRequest, NotFoundError
from ..storage.repositories.credentials import CredentialRepository

STRATEGY_TYPES = ("grid", "breakout", "tick_momentum")
GATEWAY_BIN = os.environ.get("QUANT_GATEWAY_BIN", "quant-gateway")
LISTEN_PORT = int(os.environ.get("QUANT_GATEWAY_PORT", "8790"))


def _ex_symbol(symbol: str) -> str:
    """CRYPTO:BTC/USDT → BTC-USDT（OKX instId）。"""
    if not symbol.startswith("CRYPTO:"):
        raise InvalidRequest(f"网关目前只支持 CRYPTO: 标的: {symbol}")
    return symbol[7:].replace("/", "-")


def _normalize_symbols(symbols: list[str]) -> list[str]:
    out = []
    for s in symbols:
        if not s.startswith("CRYPTO:"):
            raise InvalidRequest(f"网关目前只支持 CRYPTO: 标的: {s}")
        ex = s[7:].replace("/", "-")
        if ex not in out:
            out.append(ex)
    return out


class GatewayManager:
    """单实例网关管理器（同一后端进程同时只跑一个网关）。"""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.process: subprocess.Popen | None = None
        self.running = False
        self.lock = threading.Lock()
        self.config: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []  # 最近 100 条事件
        self.stats = {"ticks": 0, "orders": 0, "fills": 0}
        self._sock: socket.socket | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_reader = threading.Event()

    # ------------------------------------------------------------------ #
    def start(
        self,
        *,
        user_id: int,
        strategy_type: str,
        params: dict[str, Any],
        symbols: list[str],
        sandbox: bool,
        credential_id: int | None,
        bar_interval_sec: int,
        risk_rules: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        with self.lock:
            if self.running:
                raise ConflictError("网关已在运行，请先停止")
            if strategy_type not in STRATEGY_TYPES:
                raise InvalidRequest(f"策略类型只支持 {STRATEGY_TYPES}")

            symbols_ex = _normalize_symbols(symbols)
            if not symbols_ex:
                raise InvalidRequest("请至少选择一个 CRYPTO: 标的")

            env = os.environ.copy()
            if not sandbox:
                if credential_id is None:
                    raise InvalidRequest("真实资金模式需要选择 broker 凭据")
                with self.ctx.db.session() as session:
                    cred = CredentialRepository(session).get(credential_id, owner_id=user_id)
                    if cred is None:
                        raise NotFoundError("凭据不存在或不属于你")
                    if cred.kind != "broker":
                        raise InvalidRequest("该凭据不是券商凭据（kind 应为 broker）")
                    payload = CredentialRepository(session).reveal(cred)
                env["QUANT_OKX_API_KEY"] = str(payload.get("api_key") or payload.get("apiKey") or "")
                env["QUANT_OKX_SECRET"] = str(payload.get("secret") or "")
                env["QUANT_OKX_PASSPHRASE"] = str(payload.get("password") or payload.get("passphrase") or "")

            audit_dir = self.ctx.settings.data_path / "gateway"
            audit_dir.mkdir(parents=True, exist_ok=True)
            config = {
                "mode": "live" if not sandbox else "paper",
                "symbols": symbols_ex,
                "bar_interval_sec": max(1, min(86400, int(bar_interval_sec))),
                "strategy": {"type": strategy_type, "params": params or {}},
                "risk": risk_rules or [],
                "initial_cash": 0,
                "audit_path": str(audit_dir / "audit.jsonl"),
                "listen_ip": "127.0.0.1",
                "listen_port": LISTEN_PORT,
                "auto_start": True,
                "okx": {
                    "ws_public": "wss://ws.okx.com:8443/ws/v5/public",
                    "ws_private": "wss://ws.okx.com:8443/ws/v5/private",
                    "rest_host": "www.okx.com",
                    "sandbox": sandbox,
                },
            }
            config_path = audit_dir / "config.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

            self.process = subprocess.Popen(
                [GATEWAY_BIN, str(config_path)],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.time() + 10
            while time.time() < deadline:
                if self.process.poll() is not None:
                    code = self.process.returncode
                    self.process = None
                    raise ConflictError(f"网关启动失败（退出码 {code}），请检查配置")
                try:
                    probe = socket.create_connection(("127.0.0.1", LISTEN_PORT), timeout=0.5)
                    probe.close()
                    break
                except OSError:
                    time.sleep(0.2)
            else:
                self.stop_process()
                raise ConflictError("网关启动超时（IPC 未监听）")

            self.running = True
            self.config = config
            self.events = []
            self.stats = {"ticks": 0, "orders": 0, "fills": 0}
            self._stop_reader.clear()
            self._sock = socket.create_connection(("127.0.0.1", LISTEN_PORT), timeout=5)
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()
            return {"mode": config["mode"], "symbols": symbols_ex, "strategy": strategy_type}

    def stop(self) -> dict[str, Any]:
        with self.lock:
            if not self.running:
                raise ConflictError("网关未在运行")
            return self.stop_process()

    def stop_process(self) -> dict[str, Any]:
        self._send_command({"cmd": "shutdown"})
        if self.process is not None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process = None
        self.running = False
        self._stop_reader.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        return {"stopped": True}

    def shutdown(self) -> None:
        if self.running:
            try:
                self.stop()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    def _send_command(self, cmd: dict[str, Any]) -> dict[str, Any] | None:
        if self._sock is None:
            return None
        try:
            payload = json.dumps(cmd).encode()
            self._sock.sendall(len(payload).to_bytes(4, "little") + payload)
            header = b""
            while len(header) < 4:
                chunk = self._sock.recv(4 - len(header))
                if not chunk:
                    return None
                header += chunk
            length = int.from_bytes(header, "little")
            data = b""
            while len(data) < length:
                chunk = self._sock.recv(length - len(data))
                if not chunk:
                    return None
                data += chunk
            return json.loads(data)
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def _read_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        buffer = b""
        while not self._stop_reader.is_set() and self.running:
            try:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                while len(buffer) >= 4:
                    length = int.from_bytes(buffer[:4], "little")
                    if len(buffer) < 4 + length:
                        break
                    payload = buffer[4 : 4 + length]
                    buffer = buffer[4 + length :]
                    event = json.loads(payload)
                    self._on_event(event)
            except (OSError, json.JSONDecodeError, ValueError):
                break

    def _on_event(self, event: dict[str, Any]) -> None:
        etype = event.get("type", "")
        if etype == "tick_sample":
            self.stats["ticks"] = int(event.get("ticks", self.stats["ticks"]))
        elif etype == "order":
            self.stats["orders"] += 1
        elif etype == "fill":
            self.stats["fills"] += 1
        self.events.append(event)
        if len(self.events) > 100:
            del self.events[: len(self.events) - 100]

    # ------------------------------------------------------------------ #
    def status(self) -> dict[str, Any]:
        alive = self.running and self.process is not None and self.process.poll() is None
        reply = self._send_command({"cmd": "status"}) if alive else None
        return {
            "running": alive,
            "mode": self.config.get("mode"),
            "strategy": self.config.get("strategy", {}).get("type"),
            "symbols": self.config.get("symbols", []),
            "bar_interval_sec": self.config.get("bar_interval_sec"),
            "positions": (reply or {}).get("positions", {}),
            "strategy_running": (reply or {}).get("strategy_running", False),
            "stats": dict(self.stats),
            "recent_events": list(self.events[-20:]),
            "config": self.config,
        }


__all__ = ["GatewayManager", "STRATEGY_TYPES"]
