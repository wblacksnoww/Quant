"""回测引擎：历史数据逐日推进。

数据组织方式：先把每个标的的完整历史读进内存（日线级别通常几万行，完全放得下），
再按**交易日并集**逐日推进。停牌日某个标的没有 bar，自然不会触发该标的的撮合，
与真实情况一致。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..core.clock import SimClock
from ..core.events import EventBus, EventRecorder, EventType
from ..core.portfolio import Account
from ..core.types import Bar
from ..data.store import MarketDataService
from ..execution.broker import SimBroker
from ..execution.cost import CostModel
from ..execution.matching import MatchingEngine
from ..metrics.performance import compute
from ..risk.base import DEFAULT_RULES, RiskManager
from ..strategy.base import Strategy
from ..strategy.custom_fields import NEWS_FIELDS
from ..strategy.registry import get_strategy
from .base import BaseEngine, EngineConfig, EngineResult
from .session import TradingSession, bar_from_frame

log = logging.getLogger(__name__)


class BacktestEngine(BaseEngine):
    """事件驱动回测。

    :param data: 行情门面（自带缓存与多源降级）
    :param recorder: 可选的运行记录器（Web 端用来落库 + 实时推送进度）
    """

    def __init__(
        self,
        config: EngineConfig,
        data: MarketDataService,
        *,
        recorder: Any = None,
        bus: EventBus | None = None,
        news: Any = None,
    ) -> None:
        super().__init__(config)
        self.data = data
        self.recorder = recorder
        self.news = news
        self.bus = bus or EventBus(f"backtest:{config.run_id}")
        self.engine_bus = self.bus
        self._recorder_events = EventRecorder(self.bus, limit=2000)
        self._session: TradingSession | None = None
        self._result: EngineResult | None = None

    # ------------------------------------------------------------------ #
    def run(self) -> EngineResult:
        cfg = self.config
        started = time.monotonic()
        result = EngineResult(
            run_id=cfg.run_id,
            strategy_id=cfg.strategy_id,
            config=self._config_dict(),
            started_at=datetime.now(),
        )
        self._result = result

        try:
            self._report(0.02, "加载行情数据")
            frames = self._load_frames()
            if not frames:
                raise RuntimeError("没有可用的行情数据，请先检查数据源或日期区间")

            from .. import core_native

            if core_native.native_ready(cfg.strategy_id, benchmark=cfg.benchmark):
                return self._run_native(frames, result)

            self._report(0.12, "准备策略与账户")
            session = self._build_session(frames)
            self._session = session
            session.begin()

            minute = cfg.bar_interval != "1d"
            if minute:
                # 分钟级：按时间戳逐 bar 推进（对齐所有标的的时间戳）
                stamps = sorted(set().union(*[set(frame["date"].tolist()) for frame in frames.values()]))
                total = max(1, len(stamps))
                index_map = {
                    symbol: {ts: i for i, ts in enumerate(frame["date"].tolist())}
                    for symbol, frame in frames.items()
                }
                self._report(0.15, f"开始分钟级回测，共 {total} 根 {cfg.bar_interval} bar")
                for step, ts in enumerate(stamps, start=1):
                    if self.cancelled:
                        result.status = "cancelled"
                        break
                    bars: dict[str, Bar] = {}
                    indexes: dict[str, int] = {}
                    for symbol, imap in index_map.items():
                        i = imap.get(ts)
                        if i is None:
                            continue
                        bars[symbol] = bar_from_frame(frames[symbol], i, symbol)
                        indexes[symbol] = i
                    if not bars:
                        continue
                    session.step(pd.Timestamp(ts).to_pydatetime(), bars, indexes)
                    if step % max(1, total // 100) == 0:
                        self._report(0.15 + 0.80 * step / total, f"{ts} ({step}/{total})")
            else:
                dates = self._trading_dates(frames)
                total = max(1, len(dates))
                index_map = {
                    symbol: {d: i for i, d in enumerate(frame["date"].dt.date)}
                    for symbol, frame in frames.items()
                }

                self._report(0.15, f"开始回测，共 {total} 个交易日")
                for step, day in enumerate(dates, start=1):
                    if self.cancelled:
                        result.status = "cancelled"
                        break

                    bars: dict[str, Bar] = {}
                    indexes: dict[str, int] = {}
                    for symbol, frame in frames.items():
                        i = index_map[symbol].get(day)
                        if i is None:
                            continue
                        bars[symbol] = bar_from_frame(frame, i, symbol)
                        indexes[symbol] = i
                    if not bars:
                        continue

                    moment = datetime.combine(day, datetime.min.time())
                    session.step(moment, bars, indexes)

                    if step % max(1, total // 100) == 0:
                        self._report(0.15 + 0.80 * step / total, f"{day} ({step}/{total})")

            session.finish()
            self._report(0.97, "计算绩效指标")
            result.status = result.status if result.status == "cancelled" else "finished"
            self._finalize(result, session, frames)
            self._report(1.0, "完成")

        except Exception as exc:  # noqa: BLE001 - 引擎异常要落进结果里，API 才能展示原因
            log.exception("回测失败 %s", cfg.run_id)
            result.status = "failed"
            result.error = f"{type(exc).__name__}: {exc}"
            self.bus.emit_status("failed", result.error)
        finally:
            result.finished_at = datetime.now()
            result.elapsed_seconds = time.monotonic() - started
            self._recorder_events.close()
        return result

    # ------------------------------------------------------------------ #
    def _config_dict(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "strategy_id": cfg.strategy_id,
            "symbols": cfg.symbols,
            "start": cfg.start.isoformat(),
            "end": cfg.end.isoformat(),
            "initial_cash": cfg.initial_cash,
            "params": cfg.params,
            "cost": cfg.cost,
            "risk": cfg.risk,
            "benchmark": cfg.benchmark,
            "adjust": cfg.adjust,
            "volume_limit_pct": cfg.volume_limit_pct,
            "bar_interval": cfg.bar_interval,
        }

    def _run_native(self, frames: dict[str, pd.DataFrame], result: EngineResult) -> EngineResult:
        """C++ 快速路径：数据列化后交给 quant._core，结果结构与 Python 路径一致。"""
        import numpy as np

        from .. import core_native
        from ..strategy.custom import validate_spec

        # 拼装策略：C++ 编译前先过 Python 白名单校验（错误文案与 Python 路径一致）
        if self.config.strategy_id == "custom_rule":
            try:
                validate_spec(self.config.params.get("spec"))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"拼装策略 spec 无效：{exc}") from exc

        cfg = self.config
        symbols = [s for s in cfg.symbols if s in frames and len(frames[s])]
        start_dt = pd.Timestamp(datetime.combine(cfg.start, datetime.min.time()))
        end_dt = pd.Timestamp(datetime.combine(cfg.end, datetime.max.time()))
        start_ns = int(start_dt.value)
        end_ns = int(end_dt.value)

        def cols_of(frame: pd.DataFrame) -> dict[str, np.ndarray]:
            def f(name: str) -> np.ndarray:
                if name in frame.columns:
                    values = pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype="float64")
                else:
                    values = np.full(len(frame), np.nan)
                return values

            dates = pd.to_datetime(frame["date"])
            if dates.dt.tz is not None:
                dates = dates.dt.tz_localize(None)
            ns = dates.astype("datetime64[ns]").astype("int64").to_numpy()
            return {
                "dates": ns,
                "open": f("open"),
                "high": f("high"),
                "low": f("low"),
                "close": f("close"),
                "volume": f("volume"),
                "amount": f("amount"),
                "prev_close": f("prev_close"),
                "limit_up": f("limit_up"),
                "limit_down": f("limit_down"),
            }

        arrays = [cols_of(frames[s]) for s in symbols]
        extras: list[dict[str, np.ndarray]] = []
        spec = None
        if self.config.strategy_id == "custom_rule":
            raw = cfg.params.get("spec")
            if isinstance(raw, dict):
                spec = raw
            elif isinstance(raw, str):
                import json

                spec = json.loads(raw)
        if spec is not None:
            news_fields = sorted(
                {
                    str(item.get("field"))
                    for cond in spec.get("entry", []) + spec.get("exit", [])
                    for item in (cond.get("left"), cond.get("right"))
                    if isinstance(item, dict) and str(item.get("field", "")).startswith("news_")
                }
            )
            if spec.get("version") == 2:
                news_fields = sorted(
                    {
                        str(n.get("type"))
                        for n in spec.get("nodes", [])
                        if str(n.get("type", "")).startswith("news_")
                    }
                )
            if news_fields and self.news is not None:
                for s in symbols:
                    frame = frames[s]
                    extra: dict[str, np.ndarray] = {}
                    for field in news_fields:
                        metric, window = NEWS_FIELDS[field]
                        values = [
                            self.news.news_factor_at(s, as_of=row.to_pydatetime(), window=window)
                            for row in pd.to_datetime(frame["date"])
                        ]
                        extra[field] = np.array([getattr(f, metric) for f in values], dtype="float64")
                    extras.append(extra)
        while len(extras) < len(symbols):
            extras.append({})

        progress = self._make_progress_cb()
        raw_result = core_native._native.run_backtest(
            symbols,
            cfg.bar_interval,
            start_ns,
            end_ns,
            cfg.initial_cash,
            cfg.risk_free_rate,
            CostModel.for_market(cfg.market, cfg.cost).to_dict(),
            bool(CostModel.for_market(cfg.market, cfg.cost).stamp_both_sides),
            cfg.volume_limit_pct,
            cfg.risk if cfg.risk is not None else DEFAULT_RULES,
            cfg.strategy_id,
            {**cfg.params, **({"spec": spec} if spec is not None else {})},
            [arr["dates"] for arr in arrays],
            [arr["open"] for arr in arrays],
            [arr["high"] for arr in arrays],
            [arr["low"] for arr in arrays],
            [arr["close"] for arr in arrays],
            [arr["volume"] for arr in arrays],
            [arr["amount"] for arr in arrays],
            [arr["prev_close"] for arr in arrays],
            [arr["limit_up"] for arr in arrays],
            [arr["limit_down"] for arr in arrays],
            extras,
            progress,
        )

        result.status = raw_result.get("status", "finished")
        result.equity_curve = raw_result.get("equity_curve", [])
        result.trades = raw_result.get("trades", [])
        result.orders = raw_result.get("orders", [])
        result.positions = raw_result.get("positions", [])
        result.logs = raw_result.get("logs", [])
        # metrics 仍由 Python 计算（与 Python 路径同一实现）
        curve = pd.DataFrame(result.equity_curve)
        if len(curve):
            equity = pd.Series(curve["total_equity"].to_numpy(), index=pd.to_datetime(curve["dt"]))
            from ..engine.paper import PaperEngine as _PE  # 复用成交重建，保证口径一致

            fills = [_PE._fill_from_record(t) for t in result.trades]
            result.metrics = compute(
                equity,
                trades=fills,
                initial_equity=cfg.initial_cash,
                rf_annual=cfg.risk_free_rate,
            )
        if self.recorder is not None:
            self.recorder.finalize(result)
            for fill in result.trades:
                self.recorder.record_fill(fill)
            for order in result.orders:
                self.recorder.record_order(order)
        self._report(1.0, "完成")
        return result

    def _make_progress_cb(self):
        recorder = self.recorder

        def cb(progress: float, message: str) -> None:
            if recorder is not None:
                recorder.update_progress(progress, message)

        return cb

    def _load_frames(self) -> dict[str, pd.DataFrame]:
        frames: dict[str, pd.DataFrame] = {}
        minute = self.config.bar_interval != "1d"
        for symbol in self.config.symbols:
            if minute:
                start_dt = datetime.combine(self.config.start, datetime.min.time())
                end_dt = datetime.combine(self.config.end, datetime.max.time())
                frame = self.data.minute_bars(symbol, start_dt, end_dt, freq=self.config.bar_interval)
            else:
                frame = self.data.bars(symbol, self.config.start, self.config.end)
            if frame is None or len(frame) == 0:
                log.warning("标的 %s 无数据，已跳过", symbol)
                continue
            frames[symbol] = frame.reset_index(drop=True)
        return frames

    @staticmethod
    def _trading_dates(frames: dict[str, pd.DataFrame]) -> list[date]:
        dates: set[date] = set()
        for frame in frames.values():
            dates.update(frame["date"].dt.date.tolist())
        return sorted(dates)

    def _build_session(self, frames: dict[str, pd.DataFrame]) -> TradingSession:
        cfg = self.config
        cost = CostModel.for_market(cfg.market, cfg.cost)
        account = Account(cfg.initial_cash, name=cfg.run_id)
        matching = MatchingEngine(cost, volume_limit_pct=cfg.volume_limit_pct)

        # 用首日前的收盘价初始化重估价格，避免第一天空仓时净值为 0
        for symbol, frame in frames.items():
            account.mark_price(symbol, float(frame["close"].iloc[0]))

        broker = SimBroker(
            account, self.bus, matching=matching, match_on_submit=False, name=f"backtest:{cfg.run_id}"
        )
        risk = RiskManager.from_config(cfg.risk, bus=self.bus)
        strategy_cls: type[Strategy] = get_strategy(cfg.strategy_id)

        session = TradingSession(
            strategy_cls=strategy_cls,
            strategy_params=cfg.params,
            account=account,
            broker=broker,
            risk=risk,
            clock=SimClock(cfg.start),
            bus=self.bus,
            frames=frames,
            strategy_name=cfg.strategy_id,
            recorder=self.recorder,
            news=self.news,
        )

        # 把订单/成交也转给记录器（若提供）
        if self.recorder is not None:
            self.bus.subscribe(EventType.ORDER, lambda e: self.recorder.record_order_event(e.payload))
            self.bus.subscribe(EventType.FILL, lambda e: self.recorder.record_fill_event(e.payload))
            self.bus.subscribe(EventType.LOG, lambda e: self.recorder.record_log_event(e.payload))
        return session

    # ------------------------------------------------------------------ #
    def _finalize(
        self, result: EngineResult, session: TradingSession, frames: dict[str, pd.DataFrame]
    ) -> None:
        cfg = self.config
        curve = pd.DataFrame(session.equity_curve)
        equity = pd.Series(curve["total_equity"].to_numpy(), index=pd.to_datetime(curve["dt"]))
        trades = session.fills

        benchmark_curve = None
        if cfg.benchmark and cfg.benchmark in frames:
            bench_frame = frames[cfg.benchmark]
            bench = pd.Series(bench_frame["close"].to_numpy(), index=pd.to_datetime(bench_frame["date"]))
            benchmark_curve = bench.reindex(equity.index, method="ffill").dropna()

        result.metrics = compute(
            equity,
            trades=trades,
            initial_equity=cfg.initial_cash,
            rf_annual=cfg.risk_free_rate,
            benchmark=benchmark_curve,
        )
        result.equity_curve = session.equity_curve
        result.trades = [t.to_dict() for t in trades]
        result.orders = [o.to_dict() for o in session.orders]
        result.positions = [p.to_dict() for p in session.account.positions.values() if not p.is_flat]
        result.logs = [
            {"ts": e.ts.isoformat(), **e.payload}
            for e in self._recorder_events.events
            if e.type is EventType.LOG
        ]

        if self.recorder is not None:
            self.recorder.finalize(result)

    def stop(self) -> None:
        self.cancel()

    # ------------------------------------------------------------------ #
    @property
    def session(self) -> TradingSession | None:
        return self._session


__all__ = ["BacktestEngine"]
