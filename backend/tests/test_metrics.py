"""绩效指标：敞口口径。

敞口（exposure）不是能从净值反推的量 —— 同一根净值曲线，满仓和半仓
赚出来的形状可能一模一样。所以它必须由引擎从账户快照算好传进来。
这里锁住三件事：算法正确、缺列不瞎猜、越界截断。
"""

from __future__ import annotations

import pandas as pd

from quant.metrics.performance import average_exposure, compute


def test_average_exposure_uses_position_ratio() -> None:
    curve = pd.DataFrame(
        [
            {"total_equity": 1_000_000, "position_ratio": 0.0},
            {"total_equity": 1_010_000, "position_ratio": 0.5},
            {"total_equity": 1_020_000, "position_ratio": 1.0},
        ]
    )
    assert average_exposure(curve) == 0.5


def test_average_exposure_accepts_plain_dicts() -> None:
    """调用点拿到的是 list[dict]（C++ 引擎直接给的），也要能算。"""
    assert average_exposure([{"position_ratio": 0.8}, {"position_ratio": 0.6}]) == 0.7


def test_average_exposure_returns_none_without_column() -> None:
    curve = pd.DataFrame([{"total_equity": 1_000_000}])
    assert average_exposure(curve) is None
    assert average_exposure(None) is None
    assert average_exposure(pd.DataFrame()) is None


def test_average_exposure_skips_nan() -> None:
    curve = pd.DataFrame([{"position_ratio": 0.4}, {"position_ratio": None}])
    assert average_exposure(curve) == 0.4


def test_compute_records_exposure_and_clamps() -> None:
    equity = pd.Series([1_000_000, 1_010_000, 1_020_000])
    assert compute(equity, exposure=0.75).exposure == 0.75
    # 越界不放大成 >100% 的荒唐值
    assert compute(equity, exposure=1.5).exposure == 1.0
    assert compute(equity, exposure=-0.2).exposure == 0.0


def test_compute_without_exposure_stays_zero() -> None:
    """不传就留 0，不去用 1-cash_ratio 之类的东西蒙一个。"""
    equity = pd.Series([1_000_000, 1_010_000, 1_020_000])
    assert compute(equity).exposure == 0.0
