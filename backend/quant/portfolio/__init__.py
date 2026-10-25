"""组合层：权重优化与指数成分股（point-in-time）。

这一层只做"给定收益率矩阵，求权重"和"给定日期，问当时指数里有哪些股票"，
不碰撮合、不做交易。回测/纸盘/实盘谁都需要，所以独立于 engine。
"""

from __future__ import annotations

from .optimizer import (
    METHODS,
    PortfolioConstraints,
    PortfolioError,
    PortfolioSolution,
    align_returns,
    optimize_weights,
)

__all__ = [
    "METHODS",
    "PortfolioConstraints",
    "PortfolioError",
    "PortfolioSolution",
    "align_returns",
    "optimize_weights",
]
