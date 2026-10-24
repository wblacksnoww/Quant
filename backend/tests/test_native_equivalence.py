"""C++ 与 Python 回测路径的等价性：同一输入必须产生相同成交与权益曲线。"""

from __future__ import annotations

from datetime import date

import pytest

from quant import core_native
from quant.engine import BacktestEngine, EngineConfig


def _config(strategy: str, run_id: str, **params) -> EngineConfig:
    return EngineConfig(
        strategy_id=strategy,
        symbols=["CN:600519"],
        start=date(2023, 1, 1),
        end=date(2024, 6, 28),
        initial_cash=1_000_000,
        params=params,
        run_id=run_id,
    )


def _run(context, strategy: str, run_id: str, native: bool, **params):
    if not native:
        original = core_native.HAS_NATIVE
        core_native.HAS_NATIVE = False
        try:
            return BacktestEngine(_config(strategy, run_id, **params), context.data).run()
        finally:
            core_native.HAS_NATIVE = original
    return BacktestEngine(_config(strategy, run_id, **params), context.data).run()


def _assert_equivalent(a, b) -> None:
    assert a.status == b.status == "finished"
    # 成交：方向/数量/价格/时间/已实现盈亏逐笔一致（id 允许不同）
    ta = [(t["symbol"], t["side"], t["quantity"], t["price"], t["dt"]) for t in a.trades]
    tb = [(t["symbol"], t["side"], t["quantity"], t["price"], t["dt"]) for t in b.trades]
    assert ta == tb
    pa = [round(t["realized_pnl"], 6) for t in a.trades]
    pb = [round(t["realized_pnl"], 6) for t in b.trades]
    assert pa == pytest.approx(pb, abs=1e-6)
    # 权益曲线逐点一致
    assert len(a.equity_curve) == len(b.equity_curve)
    for ea, eb in zip(a.equity_curve, b.equity_curve, strict=False):
        assert ea["dt"] == eb["dt"]
        assert ea["cash"] == pytest.approx(eb["cash"], abs=1e-6)
        assert ea["total_equity"] == pytest.approx(eb["total_equity"], abs=1e-6)
        assert ea["position_count"] == eb["position_count"]
    # 订单状态序列一致
    sa = [(o["symbol"], o["side"], o["status"], o["quantity"]) for o in a.orders]
    sb = [(o["symbol"], o["side"], o["status"], o["quantity"]) for o in b.orders]
    assert sa == sb


@pytest.mark.skipif(not core_native.HAS_NATIVE, reason="C++ 扩展未构建")
class TestNativeEquivalence:
    def test_dual_ma(self, context):
        py = _run(context, "dual_ma", "eq_py_dual", native=False, fast=5, slow=20)
        native = _run(context, "dual_ma", "eq_nat_dual", native=True, fast=5, slow=20)
        assert py.trades, "合成数据应有成交"
        _assert_equivalent(py, native)

    def test_rsi_reversion(self, context):
        py = _run(context, "rsi_reversion", "eq_py_rsi", native=False, period=7)
        native = _run(context, "rsi_reversion", "eq_nat_rsi", native=True, period=7)
        _assert_equivalent(py, native)

    def test_momentum_rotation(self, context):
        config_kwargs = {"lookback": 10, "rebalance_days": 3}
        py_config = _config("momentum_rotation", "eq_py_mom", **config_kwargs)
        py_config.symbols = ["CN:600519", "CN:000001"]
        nat_config = _config("momentum_rotation", "eq_nat_mom", **config_kwargs)
        nat_config.symbols = ["CN:600519", "CN:000001"]

        core_native.HAS_NATIVE = False
        try:
            py = BacktestEngine(py_config, context.data).run()
        finally:
            core_native.HAS_NATIVE = True
        native = BacktestEngine(nat_config, context.data).run()
        _assert_equivalent(py, native)

    def test_buy_hold(self, context):
        py = _run(context, "buy_hold", "eq_py_bh", native=False)
        native = _run(context, "buy_hold", "eq_nat_bh", native=True)
        _assert_equivalent(py, native)

    def test_custom_rule_v1(self, context):
        sma5 = {"kind": "indicator", "name": "sma", "params": {"period": 5}}
        sma20 = {"kind": "indicator", "name": "sma", "params": {"period": 20}}
        spec = {
            "version": 1,
            "description": "等价性",
            "category": "trend",
            "entry": [{"type": "cross_above", "left": sma5, "right": sma20}],
            "exit": [{"type": "cross_below", "left": sma5, "right": sma20}],
            "position_pct": 0.9,
            "stop_loss": 0.08,
            "take_profit": 0.0,
        }
        py = _run(context, "custom_rule", "eq_py_c1", native=False, spec=spec)
        native = _run(context, "custom_rule", "eq_nat_c1", native=True, spec=spec)
        _assert_equivalent(py, native)

    def test_custom_rule_with_rsi_entry(self, context):
        spec = {
            "version": 1,
            "entry": [
                {
                    "type": "lt",
                    "left": {"kind": "indicator", "name": "rsi", "params": {"period": 7}},
                    "right": {"kind": "const", "value": 45},
                }
            ],
            "exit": [
                {
                    "type": "gt",
                    "left": {"kind": "indicator", "name": "rsi", "params": {"period": 7}},
                    "right": {"kind": "const", "value": 60},
                }
            ],
            "position_pct": 0.5,
            "stop_loss": 0.0,
            "take_profit": 0.0,
        }
        py = _run(context, "custom_rule", "eq_py_c2", native=False, spec=spec)
        native = _run(context, "custom_rule", "eq_nat_c2", native=True, spec=spec)
        _assert_equivalent(py, native)
