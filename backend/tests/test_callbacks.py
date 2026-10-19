"""策略回调：on_order / on_fill 必须被会话真实调用（实盘策略依赖这条链路）。"""

from __future__ import annotations

from datetime import date

from quant.engine import BacktestEngine, EngineConfig
from quant.strategy.base import ParamSpec, Strategy
from quant.strategy.registry import register_strategy


@register_strategy
class CallbackProbe(Strategy):
    strategy_id = "callback_probe"
    display_name = "回调探针"
    param_specs = (ParamSpec("qty", "float", 100.0, 1, 1e6),)

    def __init__(self, ctx, params=None):
        super().__init__(ctx, params)
        self.orders: list[str] = []
        self.fills: list[float] = []
        self._bought = False

    def on_bar(self, bar):
        if not self._bought:
            self._bought = True
            self.buy(bar.symbol, quantity=self.params["qty"])

    def on_order(self, order):
        self.orders.append(order.status.value)

    def on_fill(self, fill):
        self.fills.append(fill.quantity)


def test_strategy_receives_order_and_fill_callbacks(context):
    cfg = EngineConfig(
        strategy_id="callback_probe",
        symbols=["600519"],
        start=date(2024, 1, 1),
        end=date(2024, 3, 31),
        initial_cash=1_000_000,
        params={"qty": 100.0},
        cost={},
        run_id="run_callbacks",
    )
    engine = BacktestEngine(cfg, context.data)
    result = engine.run()
    assert result.status == "finished"

    strategy = engine.session.strategy  # type: ignore[union-attr]
    assert strategy.orders, "on_order 至少应收到 submitted 状态"
    assert "submitted" in strategy.orders
    assert strategy.fills, "on_fill 应收到成交"
    assert strategy.fills[0] == 100.0
