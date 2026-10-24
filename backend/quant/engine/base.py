"""引擎抽象与运行结果。

引擎只负责"怎么推进时间"，交易逻辑全部在 :class:`~quant.engine.session.TradingSession`。
这也是回测与纸盘能共用一套策略/风控/撮合的架构基础。
"""

from __future__ import annotations

import abc
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..metrics.performance import PerformanceReport


@dataclass
class EngineConfig:
    """一次回测/纸盘运行的完整参数。"""

    strategy_id: str
    symbols: list[str]
    start: date
    end: date
    initial_cash: float = 100_000.0
    params: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)
    risk: list[dict[str, Any]] | None = None
    benchmark: str | None = None
    adjust: str = "qfq"
    volume_limit_pct: float = 0.10
    risk_free_rate: float = 0.0
    market: str = "CN"  # 由 symbols 推断（同一运行不允许混市场）
    run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex[:16]}")
    user_id: int | None = None
    # bar 周期：1d（默认，日线）或 1m/5m/15m/30m/60m（分钟级，仅虚拟货币支持历史回测）
    bar_interval: str = "1d"

    def validate(self) -> None:
        if not self.symbols:
            raise ValueError("至少需要一个标的")
        if self.start > self.end:
            raise ValueError("开始日期不能晚于结束日期")
        if self.initial_cash <= 0:
            raise ValueError("初始资金必须为正")
        if not self.strategy_id:
            raise ValueError("必须指定策略")
        # 标的规范化 + 单一市场校验（不同货币无法统一记账）
        from ..core.markets import parse_instrument, single_market

        instruments = [parse_instrument(s) for s in self.symbols]
        self.symbols = [inst.id for inst in instruments]
        self.market = single_market(self.symbols).value
        if self.bar_interval != "1d" and self.bar_interval not in ("1m", "5m", "15m", "30m", "60m"):
            raise ValueError(f"bar_interval 只支持 1d/1m/5m/15m/30m/60m，当前 {self.bar_interval!r}")
        if self.bar_interval != "1d" and self.market != "CRYPTO":
            raise ValueError("分钟级 bar_interval 目前仅支持虚拟货币市场")


@dataclass
class EngineResult:
    """运行结果。可直接序列化为 API 响应。"""

    run_id: str
    strategy_id: str
    status: str = "finished"
    config: dict[str, Any] = field(default_factory=dict)
    metrics: PerformanceReport = field(default_factory=PerformanceReport)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    positions: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float = 0.0

    def to_dict(self, *, max_points: int = 3000) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "strategy_id": self.strategy_id,
            "status": self.status,
            "config": self.config,
            "metrics": self.metrics.to_dict(),
            "equity_curve": _downsample(self.equity_curve, max_points),
            "trades": self.trades,
            "orders": self.orders,
            "positions": self.positions,
            "logs": self.logs[-500:],
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }


def _downsample(points: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """等间隔抽稀，保留首尾。前端画图不需要每一个点。"""
    n = len(points)
    if n <= limit:
        return points
    step = n / limit
    sampled = [points[int(i * step)] for i in range(limit)]
    if sampled[-1] is not points[-1]:
        sampled[-1] = points[-1]
    return sampled


# --------------------------------------------------------------------------- #
# 引擎基类
# --------------------------------------------------------------------------- #


class BaseEngine(abc.ABC):
    """所有引擎的公共接口。"""

    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        config.validate()
        self.bus = None  # 子类初始化
        self._cancel = threading.Event()
        self.progress: float = 0.0
        self._progress_cb: Callable[[float, str], None] | None = None

    # ------------------------------------------------------------------ #
    def cancel(self) -> None:
        """请求停止（线程安全）。"""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def on_progress(self, callback: Callable[[float, str], None]) -> None:
        self._progress_cb = callback

    def _report(self, pct: float, message: str = "") -> None:
        self.progress = max(0.0, min(1.0, pct))
        if self._progress_cb is not None:
            try:
                self._progress_cb(self.progress, message)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    @abc.abstractmethod
    def run(self) -> EngineResult:
        """同步运行到结束。"""

    @abc.abstractmethod
    def stop(self) -> None:
        """请求停止并等待资源释放。"""


def build_benchmark_curve(bench: pd.Series, equity_dates: pd.Series, initial: float) -> pd.Series:
    """把基准价格序列换算成与权益曲线同日期、同起点的净值曲线。"""
    if bench is None or len(bench) == 0:
        return pd.Series(dtype=float)
    series = pd.Series(bench.to_numpy(), index=pd.to_datetime(equity_dates)).sort_index()
    base = float(series.iloc[0]) or 1.0
    return series / base * initial


__all__ = [
    "BaseEngine",
    "EngineConfig",
    "EngineResult",
    "build_benchmark_curve",
]
