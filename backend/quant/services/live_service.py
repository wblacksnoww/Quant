"""实盘应用服务：启动/停止/暂停/继续/切换。

三重闸门保持不变：服务端总开关、sandbox/confirm_live、凭据归属与 kind。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from ..core.errors import ConflictError, InvalidRequest, NotFoundError, PermissionDenied
from ..core.events import EventBus
from ..engine.live import LiveEngine, LiveOptions
from ..execution.adapters import create_adapter
from ..news.provider import NewsFactorProvider
from ..storage.recorder import RunRecorder
from ..storage.repositories.credentials import CredentialRepository
from .backtest_service import build_engine_config
from .event_bridge import bus_bridge

log = logging.getLogger(__name__)


def _coordinator(ctx: Any) -> Any:
    return getattr(ctx, "coordinator", None)


def _lease_key(user_id: int, credential_id: int) -> str:
    return f"live:{user_id}:{credential_id}"


def _remote_leases(ctx: Any, user_id: int) -> list[dict[str, Any]]:
    coord = _coordinator(ctx)
    if coord is None:
        return []
    return [lease for lease in coord.leases.active_for_user(user_id) if lease["kind"] == "live"]


def _wire_cleanup(engine: LiveEngine, unwire: list) -> None:
    original_run = engine.run

    def _run_with_cleanup():
        try:
            return original_run()
        finally:
            for cancel in unwire:
                cancel()

    engine.run = _run_with_cleanup  # type: ignore[method-assign]


def start(ctx: Any, *, user_id: int, body: Any) -> dict[str, Any]:
    if not ctx.settings.enable_live_trading:
        raise PermissionDenied("实盘功能未开启：请先在服务端设置 QUANT_ENABLE_LIVE_TRADING=true")
    if ctx.live.running(user_id, body.credential_id):
        raise ConflictError("该券商凭证已有实盘会话在运行，请先停止")
    if not body.sandbox and not body.confirm_live:
        raise InvalidRequest("关闭测试网意味着使用真实资金，必须同时勾选 confirm_live")

    with ctx.db.session() as session:
        repo = CredentialRepository(session)
        credential = repo.get(body.credential_id, owner_id=user_id)
        if credential is None:
            raise NotFoundError("凭据不存在或不属于你")
        if credential.kind != "broker":
            raise InvalidRequest("该凭据不是券商凭据（kind 应为 broker）")
        credential_name = credential.name
        payload = repo.reveal(credential)
        from .custom_params import inject_custom_spec

        resolved_params = inject_custom_spec(body.params, user_id=user_id, session=session)

    try:
        adapter = create_adapter(body.adapter, sandbox=body.sandbox, **payload)
    except ValueError as exc:
        raise InvalidRequest(str(exc)) from exc

    config = build_engine_config(
        body,
        user_id=user_id,
        default_cash=ctx.settings.default_initial_cash,
        rf=ctx.settings.default_risk_free_rate,
        params=resolved_params,
    )
    # 实盘不回溯历史：起止日期只用于指标预热
    end = body.end or date.today()
    config.end = end
    config.start = end - timedelta(days=max(body.warmup_days * 2, 60))

    coord = _coordinator(ctx)
    key = _lease_key(user_id, body.credential_id)
    if coord is not None and not coord.acquire(
        key,
        kind="live",
        user_id=user_id,
        run_id=config.run_id,
        state={
            "strategy_id": config.strategy_id,
            "symbols": config.symbols,
            "mode": "live",
            "adapter": body.adapter,
            "sandbox": body.sandbox,
            "credential_id": body.credential_id,
        },
    ):
        raise ConflictError("该券商凭证正在其他实例运行")

    recorder = RunRecorder(
        ctx.db,
        config.run_id,
        user_id=user_id,
        kind="live",
        strategy_id=config.strategy_id,
        symbols=config.symbols,
        config={
            "strategy_id": config.strategy_id,
            "symbols": config.symbols,
            "mode": "live",
            "adapter": body.adapter,
            "sandbox": body.sandbox,
            "credential_id": body.credential_id,
            "params": config.params,
        },
    )
    recorder.start()

    bus = EventBus(f"live:{config.run_id}")
    unwire = bus_bridge(bus, ctx.hub, source="live", run_id=config.run_id, user_id=user_id)
    news = NewsFactorProvider(
        ctx.db,
        config.symbols,
        start=datetime.combine(config.start, time.min),
        end=None,
    )
    engine = LiveEngine(
        config,
        ctx.data,
        adapter,
        options=LiveOptions(
            poll_interval=body.poll_interval or ctx.settings.live_poll_interval,
            warmup_days=body.warmup_days,
        ),
        bus=bus,
        recorder=recorder,
        credential_id=body.credential_id,
        news=news,
    )
    _wire_cleanup(engine, unwire)
    try:
        ctx.live.start(user_id, body.credential_id, engine)
    except RuntimeError as exc:
        if coord is not None:
            coord.release(key)
        raise ConflictError(str(exc)) from exc
    return {
        "run_id": config.run_id,
        "mode": "sandbox" if body.sandbox else "live",
        "credential_name": credential_name,
    }


def stop(ctx: Any, *, user_id: int, credential_id: int | None = None) -> dict[str, Any]:
    engine = ctx.live.engine(user_id, credential_id)
    stopped = 0
    open_orders = 0
    run_id = engine.config.run_id if engine else None
    if ctx.live.running(user_id, credential_id):
        stopped = ctx.live.stop(user_id, credential_id)
        open_orders = len(engine.broker.open_orders) if engine and engine.broker else 0
        coord = _coordinator(ctx)
        if coord is not None and credential_id is not None:
            coord.release(_lease_key(user_id, credential_id))
    coord = _coordinator(ctx)
    if coord is not None:
        remote = [
            lease
            for lease in _remote_leases(ctx, user_id)
            if lease["owner"] != coord.instance_id
            and (credential_id is None or lease["key"] == _lease_key(user_id, credential_id))
        ]
        for lease in remote:
            coord.request(lease["key"], "stop")
            stopped += 1
            run_id = run_id or lease["run_id"]
    if stopped == 0:
        raise ConflictError("没有运行中的实盘会话" if credential_id is None else "该凭证没有运行中的实盘会话")
    return {"stopped": stopped, "open_orders": open_orders, "run_id": run_id}


def pause(ctx: Any, *, user_id: int, credential_id: int | None = None) -> None:
    if ctx.live.pause(user_id, credential_id):
        return
    coord = _coordinator(ctx)
    if coord is None:
        raise ConflictError("没有可暂停的实盘会话")
    remote = [
        lease
        for lease in _remote_leases(ctx, user_id)
        if lease["owner"] != coord.instance_id
        and (credential_id is None or lease["key"] == _lease_key(user_id, credential_id))
    ]
    if not remote:
        raise ConflictError("没有可暂停的实盘会话")
    for lease in remote:
        coord.request(lease["key"], "pause")


def resume(ctx: Any, *, user_id: int, credential_id: int | None = None) -> None:
    if ctx.live.resume(user_id, credential_id):
        return
    coord = _coordinator(ctx)
    if coord is None:
        raise ConflictError("没有已暂停的实盘会话")
    remote = [
        lease
        for lease in _remote_leases(ctx, user_id)
        if lease["owner"] != coord.instance_id
        and (credential_id is None or lease["key"] == _lease_key(user_id, credential_id))
    ]
    if not remote:
        raise ConflictError("没有已暂停的实盘会话")
    for lease in remote:
        coord.request(lease["key"], "resume")


def sessions(ctx: Any, *, user_id: int) -> list[dict[str, Any]]:
    local = ctx.live.sessions(user_id)
    coord = _coordinator(ctx)
    if coord is None:
        return local
    seen = {item.get("run_id") for item in local}
    remote = [state for state in coord.remote_sessions(user_id, "live") if state.get("run_id") not in seen]
    return local + remote


def state(ctx: Any, *, user_id: int, credential_id: int | None = None) -> dict[str, Any] | None:
    local = ctx.live.state(user_id, credential_id)
    if local is not None:
        return local
    coord = _coordinator(ctx)
    if coord is None:
        return None
    for lease in _remote_leases(ctx, user_id):
        if lease["owner"] == coord.instance_id:
            continue
        if credential_id is not None and lease["key"] != _lease_key(user_id, credential_id):
            continue
        state = lease.get("state") or {}
        if state:
            return state
    return None


def switch(
    ctx: Any,
    *,
    user_id: int,
    strategy_id: str,
    params: dict[str, Any],
    cancel_orders: bool,
) -> dict[str, Any]:
    if not ctx.live.running(user_id):
        raise ConflictError("没有运行中的实盘会话")
    engine = ctx.live.engine(user_id)
    if engine is None:
        coord = _coordinator(ctx)
        if coord is None:
            raise ConflictError("没有运行中的实盘会话")
        remote = [lease for lease in _remote_leases(ctx, user_id) if lease["owner"] != coord.instance_id]
        if not remote:
            raise ConflictError("没有运行中的实盘会话")
        with ctx.db.session() as session:
            from .custom_params import inject_custom_spec

            resolved = inject_custom_spec(params, user_id=user_id, session=session)
        for lease in remote:
            coord.request(
                lease["key"],
                "switch",
                {
                    "strategy_id": strategy_id,
                    "params": resolved,
                    "cancel_orders": cancel_orders,
                },
            )
        return {"run_id": remote[0]["run_id"], "pending": True, "remote": True}
    with ctx.db.session() as session:
        from .custom_params import inject_custom_spec

        resolved = inject_custom_spec(params, user_id=user_id, session=session)
    try:
        return engine.switch_strategy(strategy_id, resolved, cancel_orders=cancel_orders)
    except KeyError as exc:
        raise InvalidRequest(str(exc)) from exc
    except RuntimeError as exc:
        raise ConflictError(str(exc)) from exc


__all__ = [
    "pause",
    "resume",
    "sessions",
    "start",
    "state",
    "stop",
    "switch",
]
