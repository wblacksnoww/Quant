"""T+0 接线：T+0 市场（虚拟货币/美股）当日买入立即可卖，A 股保持 T+1。"""

from __future__ import annotations

from datetime import date

import pytest

from quant.core.portfolio import Account
from quant.core.types import FeeBreakdown, Fill, Side, utcnow
from quant.data.providers.synthetic import SyntheticProvider
from quant.data.store import DataStore, MarketDataService
from quant.engine import BacktestEngine, EngineConfig
from quant.strategy.base import ParamSpec, Strategy
from quant.strategy.registry import register_strategy


@register_strategy
class T0Probe(Strategy):
    """第一根 bar 买入，第二根 bar 卖出同等数量。

    T+0 市场卖出单会成交；T+1 市场卖出时可用数量为 0，``sell`` 直接放弃。
    """

    strategy_id = "t0_probe"
    display_name = "T+0 探测"
    param_specs = (ParamSpec("qty", "float", 10.0, 0.0001, 1e9, description="买入数量"),)

    def __init__(self, ctx, params=None):
        super().__init__(ctx, params)
        self._bought = False
        self._sold = False

    def on_bar(self, bar):
        if not self._bought:
            self._bought = True
            self.buy(bar.symbol, quantity=self.params["qty"])
        elif not self._sold:
            self._sold = True
            self.sell(bar.symbol, quantity=self.params["qty"])


# --------------------------------------------------------------------------- #
# 账户层
# --------------------------------------------------------------------------- #


def _fill(symbol: str, quantity: float, price: float) -> Fill:
    return Fill(
        order_id="o1",
        symbol=symbol,
        side=Side.BUY,
        quantity=quantity,
        price=price,
        dt=utcnow(),
        fees=FeeBreakdown(),
    )


def test_crypto_buy_available_same_day():
    acct = Account(100_000)
    acct.apply_fill(_fill("CRYPTO:BTC/USDT", 0.5, 100.0))
    assert acct.get_position("CRYPTO:BTC/USDT").available == pytest.approx(0.5)


def test_us_buy_available_same_day():
    acct = Account(100_000)
    acct.apply_fill(_fill("US:AAPL", 3.0, 100.0))
    assert acct.get_position("US:AAPL").available == pytest.approx(3.0)


def test_cn_buy_deferred_to_next_session():
    acct = Account(100_000)
    acct.apply_fill(_fill("CN:600519", 100.0, 100.0))
    pos = acct.get_position("CN:600519")
    assert pos.available == 0.0
    acct.on_new_session()
    assert pos.available == pytest.approx(100.0)


# --------------------------------------------------------------------------- #
# 引擎层：同一条策略在两个市场的行为分叉
# --------------------------------------------------------------------------- #


def _local_data(tmp_path) -> MarketDataService:
    """纯本地数据源（合成行情），避免测试触发真实交易所网络。"""
    store = DataStore(root=tmp_path / "store", provider=SyntheticProvider())
    return MarketDataService(store)


def _run(data, symbol: str, qty: float, run_id: str, cash: float = 1_000_000.0):
    cfg = EngineConfig(
        strategy_id="t0_probe",
        symbols=[symbol],
        start=date(2024, 1, 1),
        end=date(2024, 3, 31),
        initial_cash=cash,
        params={"qty": qty},
        cost={},
        run_id=run_id,
    )
    return BacktestEngine(cfg, data).run()


def test_crypto_engine_t0_fractional(tmp_path):
    result = _run(_local_data(tmp_path), "CRYPTO:BTC/USDT", 0.5, "run_crypto_t0")
    assert result.status == "finished"

    buys = [t for t in result.trades if t["side"] == "buy"]
    sells = [t for t in result.trades if t["side"] == "sell"]
    assert buys, "买入单应成交"
    assert buys[0]["quantity"] == pytest.approx(0.5)  # 小数数量原样成交
    assert sells, "T+0 市场当日买入应立即可卖并成交"
    assert sells[0]["quantity"] == pytest.approx(0.5)


def test_cn_engine_t1_blocks_same_day_sell(tmp_path):
    result = _run(_local_data(tmp_path), "600519", 100.0, "run_cn_t1")
    assert result.status == "finished"

    assert any(t["side"] == "buy" for t in result.trades)
    assert not any(t["side"] == "sell" for t in result.trades), "T+1 市场当日买入不可卖"
