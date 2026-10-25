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


def _uses_news_field(operand: object) -> bool:
    """该操作数是否引用了新闻因子字段。"""
    if not isinstance(operand, dict):
        return False
    field = operand.get("field")
    if isinstance(field, str) and field.startswith("news_"):
        return True
    # v2 蓝图里新闻节点直接用 type 表示
    kind = operand.get("type")
    return isinstance(kind, str) and kind.startswith("news_")


def _strip_news_conditions(spec: dict[str, Any]) -> dict[str, Any]:
    """剥掉 spec 里所有引用新闻字段的入场条件。

    只在「本次运行没有新闻数据源」时调用。缺列会让 C++ 侧读到 NaN，
    而 NaN 参与比较恒为 false —— 结果就是零成交，且没有任何报错。
    剥掉之后策略退化为纯技术信号，"新闻过滤没生效"这件事由告警负责说明。

    出场条件不剥：出场被剥会导致该清仓时不清仓，比不进场危险得多。
    """
    entry = spec.get("entry")
    if not isinstance(entry, list):
        return spec
    kept = [
        cond
        for cond in entry
        if not (_uses_news_field(cond.get("left")) or _uses_news_field(cond.get("right")))
    ]
    # 全被剥光时保留一条恒真条件，避免 C++ 侧拿到空 entry 数组报错
    if not kept:
        kept = [{"type": "gt", "left": {"kind": "field", "field": "close"}, "right": {"kind": "const", "value": 0.0}}]
    out = dict(spec)
    out["entry"] = kept
    return out


def _align_benchmark(
    symbol: str | None,
    frames: dict[str, pd.DataFrame],
    index: pd.Index,
) -> pd.Series | None:
    """把基准标的收盘价对齐到权益曲线时间轴（前向填充）。

    benchmark **不参与回测循环**：它只是另一只标的的价格序列，用来算 Beta/Alpha/
    跟踪误差。所以它既不需要进 C++，也不构成"降级回 Python"的理由 —— 之前
    ``native_ready()`` 遇到 benchmark 就返回 False 是历史遗留的判断，
    现在两条路径都能算基准，C++ 主循环不必感知它的存在。
    """
    if not symbol or symbol not in frames:
        return None
    frame = frames[symbol]
    if "close" not in frame.columns or "date" not in frame.columns or not len(frame):
        return None
    prices = pd.Series(
        pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype="float64"),
        index=pd.to_datetime(frame["date"]),
    )
    return prices.reindex(index, method="ffill").dropna()
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
        # 需要 spec 的策略（custom_rule / news_dual_ma）统一走这里生成
        spec = core_native.spec_for_strategy(cfg.strategy_id, dict(cfg.params))
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
                        # NewsFactorProvider 的接口是 factor()；早期这里误写成
                        # news_factor_at()（那是 Strategy 基类的方法），导致
                        # 拼装策略一旦用到新闻字段、走 C++ 路径就 AttributeError。
                        values = [
                            self.news.factor(s, as_of=row.to_pydatetime(), window=window)
                            for row in pd.to_datetime(frame["date"])
                        ]
                        extra[field] = np.array([getattr(f, metric) for f in values], dtype="float64")
                    extras.append(extra)
            elif news_fields:
                # spec 引用了新闻字段但这次运行没有新闻数据源。
                #
                # 不能就这么把 spec 交给 C++：缺列时 Columns::at() 返回 NaN，
                # 而 NaN 参与任何比较都是 false —— 新闻条件恒不成立，回测
                # "跑通但零成交"，看起来像策略失效，实际是数据缺失。
                # （neutral 因子是 sentiment=0/confidence=0，与缺列不是一回事。）
                #
                # 正确做法：显式剥掉新闻条件，让策略退化成纯技术信号，
                # 并把"新闻过滤未生效"这个事实明确告警出来。
                before = len(spec.get("entry", []))
                spec = _strip_news_conditions(spec)
                removed = before - len(spec.get("entry", []))
                message = (
                    f"回测引用了新闻字段 {news_fields}，但本次运行没有新闻数据源；"
                    f"已剥掉 {removed} 条新闻入场条件，策略退化为纯技术信号。"
                    f"如需新闻过滤，请在「新闻」页录入数据，或传入 news=NewsFactorProvider。"
                )
                log.warning(message)
                # 只写 log 是不够的：日志文件用户看不到，结果里也没有痕迹，
                # 于是"新闻过滤没生效"这件事对使用者完全不可见。必须同时
                # 落进 result.logs（进 API 响应与前端运行日志面板）。
                result.logs.append(
                    {
                        "ts": datetime.now().isoformat(),
                        "level": "warning",
                        "message": message,
                    }
                )
        # --- Python 算子列（研究通道）------------------------------------- #
        #
        # 算子用 Python 写、全序列预计算、作为 extras 列喂给 C++。C++ 主循环
        # 不需要任何改动：它从 Columns::at() 按列名取值，取不到返回 NaN。
        # 这样"试一个新指标"不必改 C++、重编译、重启 —— 研究阶段最频繁的动作
        # 从几分钟降到秒级。代价是只有 Python 通道能算，进生产要重写成 C++。
        if spec is not None:
            from ..strategy.operators import (
                compute_operator,
                operator_fields_in_spec,
                registered,
                resolve_operator,
            )

            op_fields = operator_fields_in_spec(spec)
            if op_fields:
                # 先整体体检，再统一告警。逐个算的时候各自打 log 用户看不到，
                # 结果是"策略不成交但不知道为什么"。
                problems = {f: resolve_operator(f) for f in op_fields}
                problems = {f: why for f, why in problems.items() if why}

                for s_idx, s in enumerate(symbols):
                    while len(extras) <= s_idx:
                        extras.append({})
                    for field_name in op_fields:
                        if field_name in problems:
                            # 问题算子留空列（等价于全 NaN），不阻断其它算子
                            continue
                        extras[s_idx][field_name] = compute_operator(frames[s], field_name)

                if problems:
                    detail = "；".join(f"{f} → {why}" for f, why in problems.items())
                    message = (
                        f"spec 引用了 {len(problems)} 个不可用的 Python 算子字段：{detail}。"
                        f"这些字段全为 NaN，相关条件恒不成立，回测可能零成交。"
                        f"可用算子：{sorted(registered())}。"
                    )
                    log.warning(message)
                    result.logs.append(
                        {"ts": datetime.now().isoformat(), "level": "error", "message": message}
                    )

                usable = [f for f in op_fields if f not in problems]
                if usable:
                    result.logs.append(
                        {
                            "ts": datetime.now().isoformat(),
                            "level": "info",
                            "message": (
                                f"使用 {len(usable)} 个 Python 算子字段：{usable}。"
                                f"研究通道产物 —— 进生产需在 C++ 重写并过等价性测试。"
                            ),
                        }
                    )

        while len(extras) < len(symbols):
            extras.append({})

        progress = self._make_progress_cb()
        cost_model = cfg.cost_model() if hasattr(cfg, "cost_model") else CostModel.for_market(cfg.market, cfg.cost)
        raw_result = core_native._native.run_backtest(
            symbols,
            cfg.bar_interval,
            start_ns,
            end_ns,
            cfg.initial_cash,
            cfg.risk_free_rate,
            cost_model.to_dict(),
            bool(cost_model.stamp_both_sides),
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
            int(cfg.max_order_age_days),
        )

        result.status = raw_result.get("status", "finished")
        result.equity_curve = raw_result.get("equity_curve", [])
        result.trades = raw_result.get("trades", [])
        result.orders = raw_result.get("orders", [])
        result.positions = raw_result.get("positions", [])
        # C++ 引擎的日志之外，还要保留 Python 侧在调用前记的告警
        # （例如"新闻字段被剥掉"）—— 否则这些提示会被整体覆盖掉，
        # 用户在运行日志里看不到"策略已退化"这件事。
        result.logs = list(raw_result.get("logs", [])) + list(result.logs)
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
                benchmark=_align_benchmark(cfg.benchmark, frames, equity.index),
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
        cost = cfg.cost_model() if hasattr(cfg, "cost_model") else CostModel.for_market(cfg.market, cfg.cost)
        account = Account(cfg.initial_cash, name=cfg.run_id)
        matching = MatchingEngine(
            cost, volume_limit_pct=cfg.volume_limit_pct, impact_coef=cfg.impact_coef
        )

        # 用首日前的收盘价初始化重估价格，避免第一天空仓时净值为 0
        for symbol, frame in frames.items():
            account.mark_price(symbol, float(frame["close"].iloc[0]))

        broker = SimBroker(
            account,
            self.bus,
            matching=matching,
            match_on_submit=False,
            name=f"backtest:{cfg.run_id}",
            max_order_age_days=cfg.max_order_age_days,
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

        benchmark_curve = _align_benchmark(cfg.benchmark, frames, equity.index)

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
