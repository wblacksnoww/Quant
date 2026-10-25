"""绩效指标：把权益曲线与成交记录折算成投资者看得懂的一组数字。

口径说明：

* 年化收益用 CAGR（复利），不是"平均收益 × 252"。
* 夏普用日收益、无风险利率按年化输入，再按 √252 折算。
* 最大回撤按**权益曲线**（含持仓市值）计算，不是按已实现盈亏。
* 胜率按"平仓笔"统计，一笔卖出若实现盈亏为正记一胜。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class PerformanceReport:
    total_return: float = 0.0
    annual_return: float = 0.0
    volatility: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_days: int = 0
    calmar: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0
    total_fees: float = 0.0
    turnover: float = 0.0
    exposure: float = 0.0
    start: str | None = None
    end: str | None = None
    initial_equity: float = 0.0
    final_equity: float = 0.0
    benchmark_return: float | None = None
    excess_return: float | None = None
    # 基准对比（补全"策略到底靠什么赚钱"这个问题）
    benchmark_annual_return: float | None = None
    annual_excess_return: float | None = None
    beta: float | None = None
    alpha: float | None = None
    information_ratio: float | None = None
    tracking_error: float | None = None
    monthly_returns: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def num(value: float | None, digits: int) -> float | None:
            """NaN/inf 一律转 None（JSON 里就是 null），前端显示 "—" 或 "∞"。"""
            if value is None:
                return None
            value = float(value)
            if not np.isfinite(value):
                return None
            return round(value, digits)

        return {
            "total_return": num(self.total_return, 6),
            "annual_return": num(self.annual_return, 6),
            "volatility": num(self.volatility, 6),
            "sharpe": num(self.sharpe, 4),
            "sortino": num(self.sortino, 4),
            "max_drawdown": num(self.max_drawdown, 6),
            "max_drawdown_days": self.max_drawdown_days,
            "calmar": num(self.calmar, 4),
            "win_rate": num(self.win_rate, 6),
            "profit_factor": num(self.profit_factor, 4),
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "avg_win": num(self.avg_win, 2),
            "avg_loss": num(self.avg_loss, 2),
            "expectancy": num(self.expectancy, 2),
            "total_fees": num(self.total_fees, 2),
            "turnover": num(self.turnover, 2),
            "exposure": num(self.exposure, 6),
            "start": self.start,
            "end": self.end,
            "initial_equity": num(self.initial_equity, 2),
            "final_equity": num(self.final_equity, 2),
            "benchmark_return": num(self.benchmark_return, 6),
            "excess_return": num(self.excess_return, 6),
            "benchmark_annual_return": num(self.benchmark_annual_return, 6),
            "annual_excess_return": num(self.annual_excess_return, 6),
            "beta": num(self.beta, 4),
            "alpha": num(self.alpha, 6),
            "information_ratio": num(self.information_ratio, 4),
            "tracking_error": num(self.tracking_error, 6),
            "monthly_returns": {k: round(v, 6) for k, v in self.monthly_returns.items()},
        }


# --------------------------------------------------------------------------- #
# 子指标
# --------------------------------------------------------------------------- #


def drawdown_series(equity: pd.Series) -> pd.Series:
    """回撤序列（负数，例如 -0.12 表示回撤 12%）。"""
    if len(equity) == 0:
        return pd.Series(dtype=float)
    peak = equity.cummax()
    return equity / peak - 1.0


def max_drawdown(equity: pd.Series) -> tuple[float, int]:
    """返回 ``(最大回撤, 最长回撤持续交易日数)``。"""
    if len(equity) < 2:
        return 0.0, 0
    dd = drawdown_series(equity)
    mdd = float(-dd.min())
    # 持续期：从峰值到恢复
    longest = current = 0
    for value, peak in zip(equity.to_numpy(), equity.cummax().to_numpy(), strict=False):
        if value >= peak - 1e-12:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return mdd, longest


def sharpe_ratio(returns: pd.Series, rf_annual: float = 0.0, periods: int = TRADING_DAYS) -> float:
    if len(returns) < 2:
        return 0.0
    excess = returns - rf_annual / periods
    std = float(excess.std(ddof=0))
    if std == 0:
        return 0.0
    return float(excess.mean() / std * np.sqrt(periods))


def sortino_ratio(returns: pd.Series, rf_annual: float = 0.0, periods: int = TRADING_DAYS) -> float:
    if len(returns) < 2:
        return 0.0
    excess = returns - rf_annual / periods
    downside = excess[excess < 0]
    if len(downside) == 0:
        return float("inf") if excess.mean() > 0 else 0.0
    dd_std = float(np.sqrt((downside**2).mean()))
    if dd_std == 0:
        return 0.0
    return float(excess.mean() / dd_std * np.sqrt(periods))


def monthly_returns(equity: pd.Series) -> dict[str, float]:
    """按自然月统计收益率。"""
    if len(equity) < 2:
        return {}
    monthly = equity.resample("ME").last()
    first = pd.Series([equity.iloc[0]], index=[equity.index[0]])
    combined = pd.concat([first, monthly]).sort_index()
    rets = combined.pct_change().dropna()
    return {idx.strftime("%Y-%m"): float(v) for idx, v in rets.items()}


def trade_stats(trades: Sequence[Any]) -> dict[str, float]:
    """按成交（Fill）统计胜负。只把卖出成交计入"平仓笔"。"""
    from ..core.types import Side

    closed = [t for t in trades if getattr(t, "side", None) is Side.SELL]
    pnls = [float(getattr(t, "realized_pnl", 0.0)) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trade_count": len(closed),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": len(wins) / len(closed) if closed else 0.0,
        "profit_factor": (gross_win / gross_loss)
        if gross_loss > 0
        else (float("inf") if gross_win > 0 else 0.0),
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
        "expectancy": (sum(pnls) / len(pnls)) if pnls else 0.0,
    }


def benchmark_analysis(
    returns: pd.Series,
    bench_returns: pd.Series,
    rf_annual: float = 0.0,
    periods: int = TRADING_DAYS,
) -> dict[str, float | None]:
    """策略相对基准的分解：Beta / Alpha / 超额 / 信息比率 / 跟踪误差。

    只回答一个问题：**这个策略是在赚市场，还是在赚别的？**

    * ``beta > 1`` 且 ``alpha ≈ 0``：本质上就是加杠杆买指数，不是策略。
    * ``alpha`` 显著为正：有真实选股/择时能力。
    * ``information_ratio`` 衡量超额收益的稳定性（越高越不是撞运气）。
    """
    joined = pd.concat([returns, bench_returns], axis=1, join="inner").dropna()
    if len(joined) < 2:
        return {}
    r = joined.iloc[:, 0]
    b = joined.iloc[:, 1]

    var_b = float(b.var(ddof=0))
    beta = float(r.cov(b) / var_b) if var_b > 0 else None

    # 年化 Alpha（Jensen's alpha，日频口径）
    rf_daily = rf_annual / periods
    if beta is not None:
        alpha_daily = float(r.mean() - rf_daily - beta * (b.mean() - rf_daily))
        alpha = alpha_daily * periods
    else:
        alpha = None

    diff = r - b
    te = float(diff.std(ddof=0) * np.sqrt(periods))
    ir = float(diff.mean() / diff.std(ddof=0) * np.sqrt(periods)) if diff.std(ddof=0) > 0 else None

    return {
        "beta": beta,
        "alpha": alpha,
        "tracking_error": te,
        "information_ratio": ir,
    }


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #


def compute(
    equity: pd.Series | Iterable[float],
    *,
    trades: Sequence[Any] = (),
    initial_equity: float | None = None,
    rf_annual: float = 0.0,
    benchmark: pd.Series | None = None,
    periods: int = TRADING_DAYS,
) -> PerformanceReport:
    """计算完整绩效报告。"""
    if not isinstance(equity, pd.Series):
        series = pd.Series(list(equity), dtype=float)
    else:
        series = equity.astype(float).dropna()

    report = PerformanceReport()
    if len(series) == 0:
        return report

    start_equity = float(initial_equity if initial_equity is not None else series.iloc[0])
    final_equity = float(series.iloc[-1])
    if start_equity <= 0:
        start_equity = float(series.iloc[0]) or 1.0

    report.initial_equity = start_equity
    report.final_equity = final_equity
    report.total_return = final_equity / start_equity - 1.0

    if isinstance(series.index, pd.DatetimeIndex) and len(series) > 1:
        report.start = series.index[0].date().isoformat()
        report.end = series.index[-1].date().isoformat()

    returns = series.pct_change().dropna()
    report.volatility = float(returns.std(ddof=0) * np.sqrt(periods)) if len(returns) > 1 else 0.0

    years = len(series) / periods
    # 区间过短时年化没有意义：2 个数据点会把 10% 的涨幅放大成 16 万倍年化。
    # 少于 5 个交易日直接留 NaN，由展示层显示"—"而不是一个荒唐的数字。
    MIN_ANNUAL_POINTS = 5
    if len(series) >= MIN_ANNUAL_POINTS and years > 0 and start_equity > 0 and final_equity > 0:
        report.annual_return = float((final_equity / start_equity) ** (1 / years) - 1.0)
        if not np.isfinite(report.annual_return):
            report.annual_return = float("nan")
    else:
        report.annual_return = float("nan")

    report.sharpe = sharpe_ratio(returns, rf_annual, periods) if len(returns) > 1 else 0.0
    sortino = sortino_ratio(returns, rf_annual, periods) if len(returns) > 1 else 0.0
    report.sortino = sortino if np.isfinite(sortino) else 0.0

    report.max_drawdown, report.max_drawdown_days = max_drawdown(series)
    # 年化不可得（区间过短）时卡玛无意义
    if report.max_drawdown > 0 and not np.isnan(report.annual_return):
        report.calmar = report.annual_return / report.max_drawdown
    else:
        report.calmar = 0.0

    stats = trade_stats(trades)
    report.trade_count = int(stats["trade_count"])
    report.win_count = int(stats["win_count"])
    report.loss_count = int(stats["loss_count"])
    report.win_rate = stats["win_rate"]
    pf = stats["profit_factor"]
    # 没有亏损笔时盈亏比是无穷大，用 999 会被当成真实值参与排序；
    # 统一返回 NaN，展示层显示 "∞"。
    report.profit_factor = pf if np.isfinite(pf) else float("nan")
    report.avg_win = stats["avg_win"]
    report.avg_loss = stats["avg_loss"]
    report.expectancy = stats["expectancy"]

    report.total_fees = float(
        sum(float(getattr(t, "fees", None).total) if getattr(t, "fees", None) else 0.0 for t in trades)
    )
    report.turnover = float(sum(float(getattr(t, "gross", 0.0)) for t in trades))

    if isinstance(series.index, pd.DatetimeIndex):
        report.monthly_returns = monthly_returns(series)

    if benchmark is not None and len(benchmark) > 1:
        bench = benchmark.astype(float).dropna()
        if len(bench) > 1 and bench.iloc[0] > 0:
            report.benchmark_return = float(bench.iloc[-1] / bench.iloc[0] - 1.0)
            # 几何超额：长区间下算术相减会失真（200% vs 100% 真实超额 50%，算术差 100%）
            report.excess_return = (1.0 + report.total_return) / (1.0 + report.benchmark_return) - 1.0

            bench_years = len(bench) / periods
            if bench_years > 0:
                report.benchmark_annual_return = float(
                    (bench.iloc[-1] / bench.iloc[0]) ** (1 / bench_years) - 1.0
                )
                if not np.isnan(report.annual_return):
                    report.annual_excess_return = report.annual_return - report.benchmark_annual_return

            analysis = benchmark_analysis(returns, bench.pct_change(), rf_annual, periods)
            report.beta = analysis.get("beta")
            report.alpha = analysis.get("alpha")
            report.tracking_error = analysis.get("tracking_error")
            report.information_ratio = analysis.get("information_ratio")

    return report


__all__ = [
    "PerformanceReport",
    "compute",
    "drawdown_series",
    "max_drawdown",
    "monthly_returns",
    "sharpe_ratio",
    "sortino_ratio",
    "trade_stats",
]
