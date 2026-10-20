"""纸盘应用服务：启动、历史继续、暂停/继续、停止、删除。

HTTP 路由只做参数解析与审计；这里编排引擎、记录器、事件桥与会话管理器，
CLI 或后台任务也能直接复用。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from ..core.errors import ConflictError, InvalidRequest, NotFoundError, PermissionDenied
from ..core.events import EventBus
from ..engine.base import EngineConfig
from ..engine.paper import PaperEngine, PaperOptions, PaperResumeState
from ..storage.recorder import RunRecorder
from ..storage.repositories.runs import RunRepository
from .backtest_service import build_engine_config
from .event_bridge import bus_bridge

log = logging.getLogger(__name__)


def _coordinator(ctx: Any) -> Any:
    return getattr(ctx, "coordinator", None)


def _lease_key(run_id: str) -> str:
    return f"paper:{run_id}"


def _remote_leases(ctx: Any, user_id: int) -> list[dict[str, Any]]:
    coord = _coordinator(ctx)
    if coord is None:
        return []
    return [lease for lease in coord.leases.active_for_user(user_id) if lease["kind"] == "paper"]


def _wire_cleanup(engine: PaperEngine, unwire: list) -> None:
    """会话结束后释放事件桥（保持原 run 行为不变）。"""
    original_run = engine.run

    def _run_with_cleanup():
        try:
            return original_run()
        finally:
            for cancel in unwire:
                cancel()

    engine.run = _run_with_cleanup  # type: ignore[method-assign]


def start(ctx: Any, *, user_id: int, body: Any) -> str:
    """启动新纸盘会话，返回 run_id。"""
    with ctx.db.session() as session:
        from .custom_params import inject_custom_spec

        resolved_params = inject_custom_spec(body.params, user_id=user_id, session=session)

    config = build_engine_config(
        body,
        user_id=user_id,
        default_cash=ctx.settings.default_initial_cash,
        rf=ctx.settings.default_risk_free_rate,
        params=resolved_params,
    )
    coord = _coordinator(ctx)
    key = _lease_key(config.run_id)
    if coord is not None and not coord.acquire(key, kind="paper", user_id=user_id, run_id=config.run_id):
        raise ConflictError("该运行已在其他实例启动")
    recorder = RunRecorder(
        ctx.db,
        config.run_id,
        user_id=user_id,
        kind="paper",
        strategy_id=config.strategy_id,
        symbols=config.symbols,
        config={
            "strategy_id": config.strategy_id,
            "symbols": config.symbols,
            "mode": body.mode,
            "params": config.params,
            "initial_cash": config.initial_cash,
            "risk": config.risk,
            "cost": config.cost,
            "benchmark": config.benchmark,
            "risk_free_rate": config.risk_free_rate,
            "replay_days": body.replay_days,
            "replay_interval": body.replay_interval,
            "poll_interval": body.poll_interval,
            "respect_market_hours": body.respect_market_hours,
        },
    )
    recorder.start()

    bus = EventBus(f"paper:{config.run_id}")
    unwire = bus_bridge(bus, ctx.hub, source="paper", run_id=config.run_id, user_id=user_id)
    engine = PaperEngine(
        config,
        ctx.data,
        options=PaperOptions(
            mode=body.mode,
            poll_interval=body.poll_interval,
            replay_days=body.replay_days,
            replay_interval=body.replay_interval,
            respect_market_hours=body.respect_market_hours,
        ),
        bus=bus,
        recorder=recorder,
    )
    _wire_cleanup(engine, unwire)
    try:
        ctx.paper.start(user_id, engine)
    except RuntimeError as exc:
        if coord is not None:
            coord.release(key)
        raise ConflictError(str(exc)) from exc
    return config.run_id


def continue_run(ctx: Any, *, user_id: int, run_id: str) -> dict[str, Any]:
    """从历史停止处继续（同一 run_id），返回恢复信息。"""
    with ctx.db.session() as session:
        record = RunRepository(session).get(run_id)
        if record is None:
            raise NotFoundError("运行记录不存在")
        if record.kind != "paper":
            raise InvalidRequest("只有纸盘会话可以从停止处继续")
        if record.user_id != user_id:
            raise PermissionDenied("无权继续该会话")
        if ctx.paper.running(user_id, run_id):
            raise ConflictError("该会话仍在运行")
        if record.status not in (
            "running",
            "stopped",
            "finished",
            "failed",
            "cancelled",
            "interrupted",
        ):
            raise ConflictError(f"状态 {record.status} 不支持继续")

        stored = dict(record.config or {})
        symbols = list(record.symbols or stored.get("symbols") or [])
        strategy_id = record.strategy_id or stored.get("strategy_id") or ""
        mode = str(stored.get("mode") or "replay")
        if mode not in ("replay", "live"):
            mode = "replay"
        params = dict(stored.get("params") or {})
        initial_cash = float(stored.get("initial_cash") or ctx.settings.default_initial_cash)
        equity = [item for item in (record.equity_curve or []) if isinstance(item, dict)]
        trades = [item for item in (record.trades or []) if isinstance(item, dict)]
        positions = [item for item in (record.positions or []) if isinstance(item, dict)]
        orders = list(record.orders_data or [])
        logs = list(record.logs or [])
        elapsed = float(record.elapsed_seconds or 0.0)

    if not equity:
        raise ConflictError("该会话没有可恢复的进度")
    try:
        last_date = datetime.fromisoformat(str(equity[-1].get("dt"))).date()
    except (TypeError, ValueError) as exc:
        raise ConflictError("无法解析上次推进的日期") from exc

    today = date.today()
    warmup_days = int(stored.get("replay_days") or 120)
    if mode == "replay":
        if last_date >= today:
            raise ConflictError("该回放已推进到最新交易日，请重新启动一个新会话")
        replay_days = min(1000, max(10, (today - last_date).days + warmup_days))
    else:
        replay_days = min(1000, max(10, warmup_days))
    start_day = today - timedelta(days=min(3650, replay_days * 2))

    try:
        config = EngineConfig(
            strategy_id=strategy_id,
            symbols=symbols,
            start=start_day,
            end=today,
            initial_cash=initial_cash,
            params=params,
            cost=dict(stored.get("cost") or {}),
            risk=stored.get("risk"),
            benchmark=stored.get("benchmark"),
            risk_free_rate=float(stored.get("risk_free_rate") or 0.0),
            run_id=run_id,
            user_id=user_id,
        )
        config.validate()
    except ValueError as exc:
        raise InvalidRequest(str(exc)) from exc

    coord = _coordinator(ctx)
    key = _lease_key(run_id)
    if coord is not None and not coord.acquire(key, kind="paper", user_id=user_id, run_id=run_id):
        raise ConflictError("该会话正在其他实例运行")

    cash = initial_cash + sum(float(item.get("cash_flow") or 0.0) for item in trades)
    resume_state = PaperResumeState(
        cash=cash,
        positions=positions,
        last_date=last_date if mode == "replay" else None,
        realized_pnl=sum(float(item.get("realized_pnl") or 0.0) for item in positions),
        total_fees=sum(float((item.get("fees") or {}).get("total") or 0.0) for item in trades),
        turnover=sum(float(item.get("gross") or 0.0) for item in trades),
        trade_count=len(trades),
        trades=trades,
    )

    replay_interval = float(stored.get("replay_interval") or 0.35)
    poll_interval = float(stored.get("poll_interval") or 5.0)
    respect_hours = bool(stored.get("respect_market_hours", True))
    run_config = {
        **stored,
        "strategy_id": config.strategy_id,
        "symbols": config.symbols,
        "mode": mode,
        "params": config.params,
        "initial_cash": config.initial_cash,
        "risk": config.risk,
        "cost": config.cost,
        "benchmark": config.benchmark,
        "risk_free_rate": config.risk_free_rate,
        "replay_days": replay_days,
        "replay_interval": replay_interval,
        "poll_interval": poll_interval,
        "respect_market_hours": respect_hours,
        "resume_count": int(stored.get("resume_count") or 0) + 1,
    }

    recorder = RunRecorder(
        ctx.db,
        run_id,
        user_id=user_id,
        kind="paper",
        strategy_id=config.strategy_id,
        symbols=config.symbols,
        config=run_config,
        seed={
            "equity": equity,
            "trades": trades,
            "orders": orders,
            "logs": logs,
            "elapsed_seconds": elapsed,
        },
    )
    recorder.start()

    bus = EventBus(f"paper:{run_id}")
    unwire = bus_bridge(bus, ctx.hub, source="paper", run_id=run_id, user_id=user_id)
    engine = PaperEngine(
        config,
        ctx.data,
        options=PaperOptions(
            mode=mode,
            poll_interval=poll_interval,
            replay_days=replay_days,
            replay_interval=replay_interval,
            respect_market_hours=respect_hours,
        ),
        bus=bus,
        recorder=recorder,
        resume=resume_state,
    )
    _wire_cleanup(engine, unwire)
    try:
        ctx.paper.start(user_id, engine)
    except RuntimeError as exc:
        if coord is not None:
            coord.release(key)
        raise ConflictError(str(exc)) from exc
    return {
        "run_id": run_id,
        "mode": mode,
        "last_date": last_date.isoformat(),
        "resume_count": run_config["resume_count"],
    }


def stop(ctx: Any, *, user_id: int, run_id: str | None = None) -> int:
    stopped = 0
    if ctx.paper.running(user_id, run_id):
        stopped = ctx.paper.stop(user_id, run_id)
        coord = _coordinator(ctx)
        if coord is not None and run_id:
            coord.release(_lease_key(run_id))
    remote = [
        lease
        for lease in _remote_leases(ctx, user_id)
        if lease["owner"] != getattr(_coordinator(ctx), "instance_id", None)
        and (run_id is None or lease["run_id"] == run_id)
    ]
    coord = _coordinator(ctx)
    if coord is not None:
        for lease in remote:
            coord.request(_lease_key(lease["run_id"]), "stop")
            stopped += 1
    if stopped == 0:
        raise ConflictError("没有运行中的纸盘会话" if run_id is None else f"运行 {run_id} 不在运行")
    return stopped


def pause(ctx: Any, *, user_id: int, run_id: str | None = None) -> None:
    coord = _coordinator(ctx)
    local = ctx.paper.pause(user_id, run_id)
    if local:
        return
    if coord is None:
        raise ConflictError("没有可暂停的纸盘会话")
    remote = [
        lease
        for lease in _remote_leases(ctx, user_id)
        if lease["owner"] != coord.instance_id and (run_id is None or lease["run_id"] == run_id)
    ]
    if not remote:
        raise ConflictError("没有可暂停的纸盘会话")
    for lease in remote:
        coord.request(_lease_key(lease["run_id"]), "pause")


def resume(ctx: Any, *, user_id: int, run_id: str | None = None) -> None:
    coord = _coordinator(ctx)
    local = ctx.paper.resume(user_id, run_id)
    if local:
        return
    if coord is None:
        raise ConflictError("没有已暂停的纸盘会话")
    remote = [
        lease
        for lease in _remote_leases(ctx, user_id)
        if lease["owner"] != coord.instance_id and (run_id is None or lease["run_id"] == run_id)
    ]
    if not remote:
        raise ConflictError("没有已暂停的纸盘会话")
    for lease in remote:
        coord.request(_lease_key(lease["run_id"]), "resume")


def delete(ctx: Any, *, user_id: int, run_id: str) -> str:
    with ctx.db.session() as session:
        repo = RunRepository(session)
        record = repo.get(run_id)
        if record is None:
            raise NotFoundError("运行记录不存在")
        if record.kind != "paper":
            raise InvalidRequest("不是纸盘运行记录")
        if record.user_id != user_id:
            raise PermissionDenied("无权删除该会话")
        if ctx.paper.running(user_id, run_id):
            raise ConflictError("会话仍在运行，请先停止")
        coord = _coordinator(ctx)
        if coord is not None and coord.active(_lease_key(run_id)):
            raise ConflictError("会话正在其他实例运行，请先停止")
        status = record.status
        repo.delete(run_id)
    return status


def sessions(ctx: Any, *, user_id: int) -> list[dict[str, Any]]:
    local = ctx.paper.sessions(user_id)
    coord = _coordinator(ctx)
    if coord is None:
        return local
    seen = {item.get("run_id") for item in local}
    remote = [state for state in coord.remote_sessions(user_id, "paper") if state.get("run_id") not in seen]
    return local + remote


def state(ctx: Any, *, user_id: int, run_id: str | None = None) -> dict[str, Any] | None:
    local = ctx.paper.state(user_id, run_id)
    if local is not None:
        return local
    coord = _coordinator(ctx)
    return coord.remote_state(user_id, run_id=run_id, kind="paper") if coord is not None else None


def switch(
    ctx: Any,
    *,
    user_id: int,
    strategy_id: str,
    params: dict[str, Any],
    cancel_orders: bool,
) -> dict[str, Any]:
    if not ctx.paper.running(user_id):
        raise ConflictError("没有运行中的纸盘会话")
    engine = ctx.paper.engine(user_id)
    if engine is None:
        coord = _coordinator(ctx)
        if coord is None:
            raise ConflictError("没有运行中的纸盘会话")
        remote = [lease for lease in _remote_leases(ctx, user_id) if lease["owner"] != coord.instance_id]
        if not remote:
            raise ConflictError("没有运行中的纸盘会话")
        with ctx.db.session() as session:
            from .custom_params import inject_custom_spec

            resolved = inject_custom_spec(params, user_id=user_id, session=session)
        for lease in remote:
            coord.request(
                _lease_key(lease["run_id"]),
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
    "continue_run",
    "delete",
    "pause",
    "resume",
    "sessions",
    "start",
    "state",
    "stop",
    "switch",
]
