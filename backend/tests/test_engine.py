"""回测引擎：确定性、无未来函数、与纸盘共享撮合。"""

from __future__ import annotations

from datetime import date

import pytest

from quant.engine import BacktestEngine, EngineConfig


def config(run_id: str = "run_test") -> EngineConfig:
    return EngineConfig(
        strategy_id="dual_ma",
        symbols=["600519"],
        start=date(2023, 1, 1),
        end=date(2024, 6, 30),
        initial_cash=1_000_000,
        params={"fast": 10, "slow": 30},
        cost={},
        run_id=run_id,
    )


def test_backtest_is_deterministic(context):
    first = BacktestEngine(config("run_a"), context.data).run()
    second = BacktestEngine(config("run_b"), context.data).run()

    assert first.status == second.status == "finished"
    assert first.metrics.total_return == pytest.approx(second.metrics.total_return)
    assert first.metrics.sharpe == pytest.approx(second.metrics.sharpe)
    assert first.metrics.final_equity == pytest.approx(second.metrics.final_equity)
    assert len(first.equity_curve) == len(second.equity_curve)
    assert [trade["dt"] for trade in first.trades] == [trade["dt"] for trade in second.trades]


def test_orders_fill_on_next_bar_no_lookahead(context):
    result = BacktestEngine(config("run_next_bar"), context.data).run()
    assert result.trades, "buy_hold / dual_ma 在该区间应有成交"

    frame = context.data.bars("600519", date(2023, 1, 1), date(2024, 6, 30))
    dates = [str(value)[:10] for value in frame["date"].tolist()]
    first_trade_day = result.trades[0]["dt"][:10]
    assert dates.index(first_trade_day) >= 1
    assert first_trade_day > dates[0]


def test_metrics_and_equity_shape(context):
    result = BacktestEngine(config("run_shape"), context.data).run()
    payload = result.to_dict()
    assert payload["status"] == "finished"
    assert len(payload["equity_curve"]) == len(result.equity_curve)
    assert payload["metrics"]["final_equity"] > 0
    assert payload["metrics"]["trade_count"] >= 0

    first = payload["equity_curve"][0]
    for key in ("dt", "cash", "total_equity", "total_return", "position_count"):
        assert key in first
    assert first["total_equity"] == pytest.approx(1_000_000, rel=1e-6)
