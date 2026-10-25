"""tick 级回测引擎：把 C++ tick 主循环接到统一的 EngineConfig / EngineResult。

用法与 BacktestEngine 一致，区别只有两点：

1. 必须提供 tick 数据源（``TickProvider``）。当前默认用
   :class:`~quant.engine.tick.SyntheticTickProvider` 从分钟线插值，
   **那是模拟数据，不能用来判断策略优劣**；它的价值是验证撮合时序、
   T+1、tick 级止损这些"机制是否正确"。
2. 走 ``bar_interval`` 之外的独立通道（``native.run_tick_backtest``），
   不与 bar 级引擎共用主循环 —— 两者撮合模型不同，混在一起会互相污染。

性能：整个 tick 序列在**一次** C++ 调用里跑完，Python 只做数据准备与结果包装。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from ..core.events import EventBus
from ..data.store import MarketDataService
from ..metrics.performance import compute
from .base import BaseEngine, EngineConfig, EngineResult
from .tick import SyntheticTickProvider, TickProvider, build_tick_frame_from_provider

log = logging.getLogger(__name__)


class TickBacktestEngine(BaseEngine):
    """tick 级回测。

    :param config: 与 bar 级回测相同的配置（策略参数走 spec）
    :param data: 行情门面，用来取分钟线喂给合成 tick 源
    :param tick_provider: tick 数据源；``None`` 时用合成源并标记为模拟
    """

    def __init__(
        self,
        config: EngineConfig,
        data: MarketDataService,
        *,
        tick_provider: TickProvider | None = None,
        bus: EventBus | None = None,
    ) -> None:
        super().__init__(config)
        self.data = data
        self.bus = bus
        self._provider = tick_provider
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
            self._report(0.02, "准备 C++ tick 引擎")
            from .. import core_native

            if not core_native.HAS_NATIVE:
                raise RuntimeError(
                    f"tick 回测需要 C++ 核心（quant._core）：{core_native.NATIVE_ERROR}"
                )

            self._report(0.08, "生成 spec")
            spec = core_native.spec_for_strategy(cfg.strategy_id, dict(cfg.params))
            if spec is None:
                # 内置策略原生实现在 bar 级 C++ 引擎里，tick 通道只认 spec；
                # 这里给一个等价 spec，避免"tick 回测不支持该策略"的硬失败。
                spec = self._spec_from_builtin(cfg.strategy_id, dict(cfg.params))

            self._report(0.15, "加载行情")
            frames = self._load_minute_frames()
            if not frames:
                raise RuntimeError("没有可用的分钟线数据，tick 回测需要分钟级历史")

            provider = self._provider
            synthetic = provider is None
            if provider is None:
                provider = SyntheticTickProvider(frames=frames, interval_seconds=60)
                log.warning(
                    "tick 回测 %s 使用合成 tick（由分钟线插值）。这是模拟数据，"
                    "不能用来判断策略优劣，仅用于验证撮合时序与机制正确性。",
                    cfg.run_id,
                )

            total = len(cfg.symbols)
            all_fills: list[dict[str, Any]] = []
            curves: list[pd.DataFrame] = []

            for i, symbol in enumerate(cfg.symbols):
                self._report(0.2 + 0.6 * i / max(1, total), f"tick 回测 {symbol}")
                tf = build_tick_frame_from_provider(provider, symbol, cfg.start, cfg.end)
                if tf.size == 0:
                    log.warning("%s 在 %s~%s 没有 tick 数据", symbol, cfg.start, cfg.end)
                    continue

                tick_cfg = self._tick_cfg(len(cfg.symbols))
                raw = core_native._native.run_tick_backtest(
                    spec, tf.ts, tf.price, tf.bid, tf.ask, tf.volume, tf.amount, tick_cfg
                )

                for f in raw["fills"]:
                    all_fills.append(
                        {
                            "symbol": symbol,
                            "side": f["side"],
                            "quantity": f["quantity"],
                            "price": f["price"],
                            "fee": f["fee"],
                            "dt": pd.Timestamp(f["ts"]).to_pydatetime().isoformat(),
                        }
                    )

                curve = pd.DataFrame(raw["equity_curve"])
                if len(curve):
                    curve["dt"] = pd.to_datetime(curve["ts"])
                    curve["symbol"] = symbol
                    curves.append(curve)
                log.info(
                    "%s tick 回测完成：%d 个 tick，%d 笔成交，warmup 跳过 %d",
                    symbol, raw["ticks_processed"], len(raw["fills"]), raw["ticks_skipped_warmup"],
                )

            self._report(0.85, "合并权益曲线")
            if not curves:
                raise RuntimeError("没有产生任何 tick 数据，请检查日期区间与数据源频率")

            # 多标的：按时间戳对齐后汇总权益
            merged = self._merge_curves(curves)
            result.equity_curve = merged.to_dict("records")
            result.trades = all_fills

            self._report(0.95, "计算绩效指标")
            equity = pd.Series(
                merged["total_equity"].to_numpy(dtype="float64"),
                index=pd.to_datetime(merged["dt"]),
            )
            result.metrics = compute(equity, initial_equity=cfg.initial_cash,
                                     rf_annual=cfg.risk_free_rate)
            if synthetic:
                # 把"这是模拟数据"这个事实记进日志，避免报告被误读
                result.logs.append(
                    {
                        "ts": datetime.now().isoformat(),
                        "level": "warning",
                        "message": "本次 tick 回测使用合成 tick（分钟线插值），非真实逐笔数据",
                    }
                )
            result.status = "finished"
            self._report(1.0, "完成")
        except Exception as exc:  # noqa: BLE001 - 引擎异常要落进结果里
            log.exception("tick 回测失败 %s", cfg.run_id)
            result.status = "failed"
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            result.finished_at = datetime.now()
            result.elapsed_seconds = time.monotonic() - started
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
            "bar_interval": "tick",
            "engine": "tick",
        }

    def _load_minute_frames(self) -> dict[str, pd.DataFrame]:
        """取分钟线（合成 tick 的原料）。

        分钟级只在虚拟货币市场可用；A 股退回日线，此时合成 tick 的粒度
        会粗到只能"一天几笔"，机制验证的价值也就打折 —— 这一点在日志里说明。
        """
        out: dict[str, pd.DataFrame] = {}
        cfg = self.config
        for symbol in cfg.symbols:
            frame = None
            if self._supports_minute(symbol):
                try:
                    start_dt = datetime.combine(cfg.start, datetime.min.time())
                    end_dt = datetime.combine(cfg.end, datetime.max.time())
                    frame = self.data.minute_bars(symbol, start_dt, end_dt, freq="1m")
                except Exception as exc:  # noqa: BLE001 - 分钟线不可用时回落日线
                    log.warning("%s 取分钟线失败（%s），回落日线", symbol, exc)
            if frame is None or len(frame) == 0:
                try:
                    frame = self.data.bars(symbol, cfg.start, cfg.end)
                except Exception as exc:  # noqa: BLE001 - 单标的失败不阻断其它标的
                    log.warning("%s 取行情失败：%s", symbol, exc)
                    continue
            if frame is not None and len(frame):
                out[symbol] = frame.reset_index(drop=True)
        return out

    def _supports_minute(self, symbol: str) -> bool:
        return symbol.upper().startswith("CRYPTO:")

    def _tick_cfg(self, n_symbols: int):
        from .. import core_native

        cfg = self.config
        tc = core_native._native.TickConfig()
        tc.initial_cash = cfg.initial_cash / max(1, n_symbols)
        tc.volume_limit_pct = cfg.volume_limit_pct
        tc.position_pct = float(cfg.params.get("position_pct", 0.95) or 0.95)
        tc.stop_loss = float(cfg.params.get("stop_loss", 0.0) or 0.0)
        tc.take_profit = float(cfg.params.get("take_profit", 0.0) or 0.0)
        cost = cfg.cost_model()
        tc.fee_rate = float(getattr(cost, "commission_rate", 0.0003) or 0.0003)
        tc.stamp_tax = float(getattr(cost, "stamp_tax", 0.001) or 0.001)
        tc.slippage_bps = float(getattr(cost, "slippage_bps", 0.0) or 0.0)
        return tc

    @staticmethod
    def _spec_from_builtin(strategy_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """把内置策略参数翻成等价 spec（tick 通道只认 spec）。"""
        fast = int(params.get("fast", 10) or 10)
        slow = int(params.get("slow", 30) or 30)
        sma_fast = {"kind": "indicator", "name": "sma", "params": {"period": fast}}
        sma_slow = {"kind": "indicator", "name": "sma", "params": {"period": slow}}

        if strategy_id in ("dual_ma", "news_dual_ma"):
            return {
                "version": 1,
                "description": f"{strategy_id} → spec",
                "category": "trend",
                "entry": [{"type": "cross_above", "left": sma_fast, "right": sma_slow}],
                "exit": [{"type": "cross_below", "left": sma_fast, "right": sma_slow}],
                "position_pct": float(params.get("position_pct", 0.95) or 0.95),
                "stop_loss": float(params.get("stop_loss", 0.0) or 0.0),
                "take_profit": float(params.get("take_profit", 0.0) or 0.0),
            }
        if strategy_id == "rsi_reversion":
            period = int(params.get("period", 14) or 14)
            oversold = float(params.get("oversold", 30) or 30)
            overbought = float(params.get("overbought", 70) or 70)
            rsi = {"kind": "indicator", "name": "rsi", "params": {"period": period}}
            return {
                "version": 1,
                "description": "rsi_reversion → spec",
                "category": "mean_reversion",
                "entry": [{"type": "lt", "left": rsi, "right": {"kind": "const", "value": oversold}}],
                "exit": [{"type": "gt", "left": rsi, "right": {"kind": "const", "value": overbought}}],
                "position_pct": float(params.get("position_pct", 0.90) or 0.90),
                "stop_loss": float(params.get("stop_loss", 0.0) or 0.0),
                "take_profit": float(params.get("take_profit", 0.0) or 0.0),
            }
        if strategy_id == "buy_hold":
            return {
                "version": 1,
                "description": "buy_hold → spec",
                "category": "trend",
                "entry": [
                    {
                        "type": "gt",
                        "left": {"kind": "field", "field": "close"},
                        "right": {"kind": "const", "value": 0.0},
                    }
                ],
                "exit": [
                    {
                        "type": "lt",
                        "left": {"kind": "field", "field": "close"},
                        "right": {"kind": "const", "value": 0.0},
                    }
                ],
                "position_pct": 1.0,
                "stop_loss": 0.0,
                "take_profit": 0.0,
            }
        # momentum_rotation 需要横截面排序，tick 通道暂不支持
        raise ValueError(f"tick 回测暂不支持策略 {strategy_id}（需要横截面排序）")

    @staticmethod
    def _merge_curves(curves: list[pd.DataFrame]) -> pd.DataFrame:
        """多标的权益曲线按时间戳合并（求和），并计算总权益。"""
        if len(curves) == 1:
            df = curves[0].copy()
            df["total_equity"] = df["equity"]
            return df[["dt", "equity", "cash", "position", "price", "total_equity"]]

        merged: pd.DataFrame | None = None
        for c in curves:
            part = c[["dt", "equity", "cash", "position"]].copy()
            part = part.sort_values("dt")
            if merged is None:
                merged = part
            else:
                merged = pd.merge_asof(
                    merged.sort_values("dt"), part.sort_values("dt"),
                    on="dt", direction="backward", suffixes=("", "_r"),
                )
                for col in ("equity", "cash", "position"):
                    merged[col] = merged[col].fillna(0.0) + merged[f"{col}_r"].fillna(0.0)
                merged = merged.drop(columns=[c for c in merged.columns if c.endswith("_r")])
        assert merged is not None
        merged["total_equity"] = merged["equity"]
        merged["price"] = 0.0
        return merged[["dt", "equity", "cash", "position", "price", "total_equity"]]

    # ------------------------------------------------------------------ #
    def stop(self) -> None:
        self.cancel()

    @property
    def result(self) -> EngineResult | None:
        return self._result


__all__ = ["TickBacktestEngine"]
