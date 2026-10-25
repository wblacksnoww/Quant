"""组合权重优化。

输入是一组标的的日收益率矩阵，输出是一组权重。不依赖 scipy —— 五种方法的
闭式解或迭代都足够简单，多引一个重依赖不划算。

方法各自的适用场景（写在这里，是因为「用哪个」比「怎么算」更容易选错）：

``equal_weight``
    不知道各标的预期收益、也不相信协方差估计时最稳的选择。学术上它长期
    难以被超越，实盘里也最难出错。

``inverse_vol``
    按波动率倒数分配。只要"低波动资产多给点"这个判断，不需要协方差矩阵。

``risk_parity``
    每个标的对组合总风险的贡献相等。比 inverse_vol 多考虑了相关性，
    在资产相关性差异大时明显不同。

``min_variance``
    只求组合方差最小，完全无视预期收益。协方差估计噪声大时容易把权重
    全压到一两个低波动标的上。

``max_sharpe``
    切线组合。对预期收益的估计极其敏感 —— 用历史均值当预期收益时，
    结果常常是把 80% 仓位压到最近涨得最好的那只上。谨慎使用。

所有方法的权重都满足：非负、和为 1、单标的不超过上限。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "METHODS",
    "PortfolioConstraints",
    "PortfolioError",
    "PortfolioSolution",
    "optimize_weights",
]

METHODS: dict[str, str] = {
    "equal_weight": "等权",
    "inverse_vol": "波动率倒数",
    "risk_parity": "风险平价",
    "min_variance": "最小方差",
    "max_sharpe": "最大夏普",
}

TRADING_DAYS = 252
# 协方差加的对角 ridge（相对量级）：单一标的或样本极少时矩阵接近奇异，
# 伪逆虽然能算，但会给出权重极端、完全不可用的解。加一点 ridge 更稳。
_RIDGE = 1e-6


class PortfolioError(ValueError):
    """无法完成优化（数据不足、方法未知等）。"""


@dataclass(frozen=True)
class PortfolioConstraints:
    min_weight: float = 0.0
    max_weight: float = 1.0
    long_only: bool = True

    def validate(self, count: int) -> None:
        if not self.long_only:
            raise PortfolioError("当前只支持多头组合（权重非负）")
        if self.min_weight < 0:
            raise PortfolioError("最小权重不能为负")
        if self.max_weight > 1:
            raise PortfolioError("单标的上限不能超过 100%")
        if self.max_weight < self.min_weight:
            raise PortfolioError("单标的上限不能小于下限")
        if count * self.max_weight < 1 - 1e-9:
            raise PortfolioError(
                f"{count} 个标的、单标的上限 {self.max_weight:.0%}，加起来到不了 100%，约束互相矛盾"
            )


@dataclass
class PortfolioSolution:
    method: str
    weights: dict[str, float]
    expected_return: float = 0.0
    volatility: float = 0.0
    sharpe: float = 0.0
    diversification: float = 0.0
    note: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "method_label": METHODS.get(self.method, self.method),
            "weights": self.weights,
            "expected_return": self.expected_return,
            "volatility": self.volatility,
            "sharpe": self.sharpe,
            "diversification": self.diversification,
            "note": self.note,
            "diagnostics": self.diagnostics,
        }


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #


def _annualized_moments(returns: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """年化均值与协方差。返回 (mu, cov)。"""
    mu = returns.mean().to_numpy(dtype="float64") * TRADING_DAYS
    cov = returns.cov().to_numpy(dtype="float64") * TRADING_DAYS
    # 单列时 cov 返回标量，统一成二维
    if cov.ndim == 1:
        cov = cov.reshape(1, 1)
    # 数值上保证对称（浮点误差会让协方差矩阵轻微非对称，求逆后放大）
    cov = (cov + cov.T) / 2
    scale = float(np.trace(cov)) / max(len(cov), 1)
    if scale > 0:
        cov = cov + np.eye(len(cov)) * scale * _RIDGE
    return mu, cov


def _normalize(weights: np.ndarray) -> np.ndarray:
    total = float(weights.sum())
    if not np.isfinite(total) or total <= 1e-12:
        return np.full(len(weights), 1.0 / len(weights))
    return weights / total


def _apply_constraints(weights: np.ndarray, constraints: PortfolioConstraints) -> np.ndarray:
    """把权重投影到约束集上。

    clip 之后和不再等于 1，所以要反复"归一 → clip"，直到浮点意义上稳定。
    直接一次 clip + 归一不收敛：归一可能又把某个权重顶到上限之上。
    """
    w = np.clip(
        _normalize(np.abs(weights) if constraints.long_only else weights),
        constraints.min_weight,
        constraints.max_weight,
    )
    for _ in range(100):
        total = w.sum()
        if abs(total - 1.0) < 1e-12:
            break
        # 把差额按"还有余量"的标的均摊
        deficit = 1.0 - total
        if deficit > 0:
            room = constraints.max_weight - w
            room = np.where(room > 1e-12, room, 0.0)
        else:
            room = w - constraints.min_weight
            room = np.where(room > 1e-12, room, 0.0)
        if room.sum() <= 1e-12:
            break
        w = w + deficit * room / room.sum()
        w = np.clip(w, constraints.min_weight, constraints.max_weight)
    return w


def _inverse(matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """解 Σ⁻¹v；奇异时退回伪逆。"""
    try:
        solution = np.linalg.solve(matrix, vector)
    except np.linalg.LinAlgError:
        solution = np.linalg.pinv(matrix) @ vector
    if not np.all(np.isfinite(solution)):
        solution = np.linalg.pinv(matrix) @ vector
    return solution


def _risk_parity_weights(cov: np.ndarray, *, max_iter: int = 2000, tol: float = 1e-10) -> np.ndarray:
    """风险平价：让每个标的的风险贡献相等。

    用乘性更新（Spinu 的简化形式）：w ← w * sqrt(目标贡献 / 当前贡献)。
    目标贡献取组合总风险 / n。收敛比通用非线性求解器慢，但不需要 scipy，
    且对本问题足够稳定。
    """
    n = len(cov)
    weights = np.full(n, 1.0 / n)
    for _ in range(max_iter):
        marginal = cov @ weights
        contribution = weights * marginal
        total_var = float(weights @ marginal)
        if total_var <= 1e-18:
            break
        target = total_var / n
        ratio = target / np.maximum(contribution, 1e-16)
        updated = weights * np.sqrt(ratio)
        updated = np.maximum(updated, 1e-12)
        updated = _normalize(updated)
        if np.max(np.abs(updated - weights)) < tol:
            weights = updated
            break
        weights = updated
    return weights


def _diversification_ratio(weights: np.ndarray, cov: np.ndarray) -> float:
    """分散化比率：加权平均波动 / 组合波动。1 表示完全没有分散效果。"""
    vols = np.sqrt(np.maximum(np.diag(cov), 0.0))
    portfolio_vol = float(np.sqrt(max(weights @ cov @ weights, 0.0)))
    if portfolio_vol <= 1e-18:
        return 1.0
    return float((weights @ vols) / portfolio_vol)


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #


def _round_preserving_sum(
    weights: np.ndarray, constraints: PortfolioConstraints, digits: int = 6
) -> np.ndarray:
    """四舍五入到定点小数，同时保证和仍精确为 1。

    直接把每项 round 到 6 位，总和会是 0.999999 这种值。实盘按权重下单时
    这点差额会变成"最后一笔少买一点"，或者更糟：各项加起来超过 100% 导致
    最后一只股票资金不足被拒单。差额补给权重最大的那个（它对结果影响最小，
    且通常还有余量）。
    """
    rounded = np.round(weights, digits)
    delta = round(1.0 - float(rounded.sum()), digits)
    if delta != 0:
        for index in np.argsort(-rounded):
            candidate = round(float(rounded[index]) + delta, digits)
            if constraints.min_weight - 1e-12 <= candidate <= constraints.max_weight + 1e-12:
                rounded[index] = candidate
                break
    return rounded


def optimize_weights(
    returns: pd.DataFrame,
    method: str = "risk_parity",
    *,
    risk_free_rate: float = 0.0,
    constraints: PortfolioConstraints | None = None,
) -> PortfolioSolution:
    """按指定方法求权重。

    ``returns`` 是「日期 × 标的」的日收益率（小数，不是百分比）。缺失值已由
    调用方对齐（通常取交集日期，见 :func:`align_returns`）。
    """
    if method not in METHODS:
        raise PortfolioError(f"未知方法 {method!r}，可选：{', '.join(METHODS)}")
    if returns.empty:
        raise PortfolioError("收益率矩阵为空")

    symbols = [str(c) for c in returns.columns]
    if len(symbols) == 1:
        solution = PortfolioSolution(method=method, weights={symbols[0]: 1.0})
        solution.note = "只有一个标的，权重必然是 100%"
        return solution
    if len(returns) < 2:
        raise PortfolioError(f"至少需要 2 个交易日的数据，当前 {len(returns)} 行")

    constraints = constraints or PortfolioConstraints()
    constraints.validate(len(symbols))

    mu, cov = _annualized_moments(returns)
    ones = np.ones(len(symbols))
    note = ""

    if method == "equal_weight":
        raw = ones.copy()
    elif method == "inverse_vol":
        vols = np.sqrt(np.maximum(np.diag(cov), 1e-18))
        raw = 1.0 / vols
    elif method == "risk_parity":
        raw = _risk_parity_weights(cov)
    elif method == "min_variance":
        raw = _inverse(cov, ones)
        if np.any(raw < 0):
            note = "最小方差解出现负权重（需要做空），已按多头约束截断后重新归一"
    else:  # max_sharpe
        excess = mu - risk_free_rate
        if np.all(excess <= 0):
            # 所有标的的预期超额收益都非正：切线组合不存在（会得到全做空），
            # 退回最小方差是唯一有意义的答案。
            raw = _inverse(cov, ones)
            note = "所有标的的预期超额收益均为非正，最大夏普退化为最小方差组合"
        else:
            raw = _inverse(cov, excess)
            if np.any(raw < 0):
                note = "最大夏普解含负权重，已按多头约束截断后重新归一（结果不再是最优解）"

    weights = _apply_constraints(raw, constraints)
    weights = _round_preserving_sum(weights, constraints)
    weight_map = {symbol: float(w) for symbol, w in zip(symbols, weights, strict=True)}

    expected = float(weights @ mu)
    volatility = float(np.sqrt(max(weights @ cov @ weights, 0.0)))
    sharpe = (expected - risk_free_rate) / volatility if volatility > 1e-12 else 0.0

    return PortfolioSolution(
        method=method,
        weights=weight_map,
        expected_return=expected,
        volatility=volatility,
        sharpe=sharpe,
        diversification=_diversification_ratio(weights, cov),
        note=note,
        diagnostics={
            "observations": int(len(returns)),
            "symbols": len(symbols),
            "max_weight": float(weights.max()),
            # 有效标的数：1/Σw²。权重越集中越接近 1。
            "effective_holdings": float(1.0 / max(float(weights @ weights), 1e-12)),
        },
    )


def align_returns(frames: Mapping[str, pd.DataFrame], *, column: str = "close") -> pd.DataFrame:
    """把多个标的的价量帧对齐成收益率矩阵。

    用**日期交集**而不是并集后前向填充：补出来的价格是"没交易却按昨天的价格
    算"，会人为压低波动率，优化结果会严重偏向那些经常停牌的标的。
    """
    series: dict[str, pd.Series] = {}
    for symbol, frame in frames.items():
        if frame is None or frame.empty or column not in frame.columns:
            continue
        data = frame[["date", column]].copy()
        data["date"] = pd.to_datetime(data["date"]).dt.normalize()
        data = data.drop_duplicates(subset="date", keep="last").set_index("date")
        series[symbol] = pd.to_numeric(data[column], errors="coerce")

    if not series:
        raise PortfolioError("没有可用的价格序列")

    prices = pd.DataFrame(series).sort_index().dropna(how="any")
    if len(prices) < 3:
        raise PortfolioError("对齐后不足 3 个交易日，无法估计协方差")
    returns = prices.pct_change().dropna(how="any")
    if len(returns) < 2:
        raise PortfolioError("对齐后不足 2 个收益率观测")
    return returns
