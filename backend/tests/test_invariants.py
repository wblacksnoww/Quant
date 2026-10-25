"""不变量测试：不管用哪个引擎、哪套参数，这些物理约束都必须成立。

与 ``test_native_equivalence.py``（Python 输出 == C++ 输出）的分工：

* 等价性测试保证**两套实现一致**，但两边一致地错时它会照样通过
  （跳空负现金 bug 就是这样潜伏的）。
* 本文件测的是**语义正确性**——现金不为负、成交不超过 bar 成交量、
  T+1 当日买入不可卖、GTC 挂单不会永久冻结资金。这些断言独立于实现，
  是真正能抓住"两边一起错"的那类 bug 的防线。

新增引擎/策略/优化时，先让这些测试通过，再谈等价性。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from quant.core.portfolio import Account
from quant.core.types import Bar, Fill, FeeBreakdown, Order, OrderType, Side, TimeInForce
from quant.execution.broker import SimBroker
from quant.execution.cost import CostModel
from quant.execution.matching import MatchingEngine


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def make_broker(cash: float = 100_000, *, volume_limit_pct: float = 1.0, age_days: int = 30):
    account = Account(cash)
    matching = MatchingEngine(CostModel(), volume_limit_pct=volume_limit_pct)
    broker = SimBroker(
        account, matching=matching, match_on_submit=False, max_order_age_days=age_days
    )
    return account, broker


def bar(symbol: str, day: int, *, open_: float, high: float = None, low: float = None,
        close: float = None, volume: float = 10_000_000) -> Bar:
    return Bar(
        symbol=symbol,
        dt=datetime(2024, 1, day),
        open=open_,
        high=high if high is not None else open_,
        low=low if low is not None else open_,
        close=close if close is not None else open_,
        volume=volume,
        amount=open_ * volume,
    )


def fill(symbol: str, side: Side, qty: float, price: float, *, commission: float = 5.0) -> Fill:
    return Fill(
        order_id="t",
        symbol=symbol,
        side=side,
        quantity=qty,
        price=price,
        dt=datetime(2024, 1, 2),
        fees=FeeBreakdown(commission=commission, stamp_tax=0.0, transfer_fee=0.0, slippage=0.0),
    )


# --------------------------------------------------------------------------- #
# 不变量 1：现金永不为负（跳空保护）
# --------------------------------------------------------------------------- #
class TestCashNeverNegative:
    def test_gap_up_caps_quantity(self):
        """跳空 +30% 高开：原数量买不起时应缩减，而不是把现金扣成负数。"""
        account, broker = make_broker(100_000)
        account.mark_price("CN:600000", 10.0)
        order = Order(symbol="CN:600000", side=Side.BUY, quantity=9_700,
                      order_type=OrderType.MARKET)
        broker.submit(order)

        broker.on_bar(bar("CN:600000", 2, open_=13.0))

        assert account.cash >= 0, f"现金变成负数：{account.cash}"
        assert order.filled_quantity > 0, "应当至少成交一部分"
        assert order.filled_quantity < 9_700, "跳空后应缩减成交数量"
        assert order.meta.get("cash_capped") is True
        # 剩余部分已无力买入，订单应被终止而不是继续占着冻结额度
        assert not order.is_open
        assert account.frozen_cash == 0, "跳空终止后必须解冻剩余资金"

    def test_extreme_gap_blocks_order(self):
        """跳空到完全买不起一手：订单被明确阻断，资金分文不动。"""
        account, broker = make_broker(10_000)
        account.mark_price("CN:600000", 10.0)
        order = Order(symbol="CN:600000", side=Side.BUY, quantity=900,
                      order_type=OrderType.MARKET)
        broker.submit(order)
        before = account.cash

        broker.on_bar(bar("CN:600000", 2, open_=500.0))

        assert account.cash == before, "未成交不应扣款"
        assert order.filled_quantity == 0
        assert order.meta.get("last_block") == "insufficient_cash"
        # 无法成交即中止，资金必须解冻可用
        assert account.frozen_cash == 0
        assert account.available_cash == pytest.approx(before, abs=1e-6)

    def test_account_rejects_overdraft_fill(self):
        """账户层是最后一道闸门：绕过撮合直接结算超额买单必须抛错。"""
        account = Account(1_000)
        with pytest.raises(Exception):
            account.apply_fill(fill("CN:600000", Side.BUY, 1_000, 10.0))
        assert account.cash == 1_000, "抛错后账户不应被改动"

    @pytest.mark.parametrize("gap", [1.05, 1.2, 1.5, 2.0, 5.0])
    def test_various_gap_sizes(self, gap: float):
        """各种跳空幅度下都不允许负现金。"""
        account, broker = make_broker(100_000)
        account.mark_price("CN:600000", 10.0)
        order = Order(symbol="CN:600000", side=Side.BUY, quantity=9_800,
                      order_type=OrderType.MARKET)
        broker.submit(order)
        broker.on_bar(bar("CN:600000", 2, open_=10.0 * gap))
        assert account.cash >= -1e-6, f"跳空 {gap:.2f}x 后现金 {account.cash}"


# --------------------------------------------------------------------------- #
# 不变量 2：成交数量不超过 bar 成交量约束
# --------------------------------------------------------------------------- #
class TestFillWithinLiquidity:
    @pytest.mark.parametrize("pct", [0.01, 0.1, 0.5, 1.0])
    def test_partial_fill_respects_volume_cap(self, pct: float):
        account, broker = make_broker(10_000_000, volume_limit_pct=pct)
        account.mark_price("CN:600000", 10.0)
        volume = 1_000_000
        qty = int(volume / pct) if pct > 0 else 1_000
        order = Order(symbol="CN:600000", side=Side.BUY, quantity=qty,
                      order_type=OrderType.MARKET)
        broker.submit(order)
        broker.on_bar(bar("CN:600000", 2, open_=10.0, volume=volume))

        filled = order.filled_quantity
        assert filled <= volume * pct + 1e-6, f"成交 {filled} 超过 {volume}×{pct} 的上限"
        assert account.get_position("CN:600000").quantity == filled


# --------------------------------------------------------------------------- #
# 不变量 3：T+1 —— 当日买入不可卖
# --------------------------------------------------------------------------- #
class TestTPlusOne:
    def test_same_day_buy_not_sellable(self):
        account, broker = make_broker(100_000)
        account.mark_price("CN:600000", 10.0)
        buy = Order(symbol="CN:600000", side=Side.BUY, quantity=1_000,
                    order_type=OrderType.MARKET)
        broker.submit(buy)
        broker.on_bar(bar("CN:600000", 2, open_=10.0))

        pos = account.get_position("CN:600000")
        assert pos.quantity == 1_000
        assert pos.available == 0, "A 股当日买入不应计入可卖"

        sell = Order(symbol="CN:600000", side=Side.SELL, quantity=1_000,
                     order_type=OrderType.MARKET)
        broker.submit(sell)
        assert sell.status.value == "rejected", "T+1 市场当日买入后不应允许卖出"

    def test_next_day_becomes_sellable(self):
        account, broker = make_broker(100_000)
        account.mark_price("CN:600000", 10.0)
        broker.submit(Order(symbol="CN:600000", side=Side.BUY, quantity=1_000,
                            order_type=OrderType.MARKET))
        broker.on_bar(bar("CN:600000", 2, open_=10.0))

        account.on_new_session()  # 次日开盘解冻
        pos = account.get_position("CN:600000")
        assert pos.available == 1_000

    def test_t0_market_sellable_immediately(self):
        """美股 T+0：买入当场可卖。"""
        account, broker = make_broker(100_000)
        account.mark_price("US:AAPL", 190.0)
        broker.submit(Order(symbol="US:AAPL", side=Side.BUY, quantity=100,
                            order_type=OrderType.MARKET))
        broker.on_bar(bar("US:AAPL", 2, open_=190.0))
        pos = account.get_position("US:AAPL")
        assert pos.available == pos.quantity


# --------------------------------------------------------------------------- #
# 不变量 4：GTC 挂单不会永久冻结资金
# --------------------------------------------------------------------------- #
class TestOrderAgeLimit:
    def test_gtc_expires_and_releases_funds(self):
        account, broker = make_broker(100_000, age_days=5)
        account.mark_price("CN:600000", 10.0)
        order = Order(symbol="CN:600000", side=Side.BUY, quantity=5_000,
                      order_type=OrderType.LIMIT, limit_price=9.5, tif=TimeInForce.GTC)
        order.created_at = datetime(2024, 1, 1)
        broker.submit(order)
        frozen_before = account.frozen_cash
        assert frozen_before > 0

        for day in range(2, 12):
            broker.on_bar(bar("CN:600000", day, open_=10.0, high=10.5, low=10.0))
            broker.end_of_day(datetime(2024, 1, day))
            if not order.is_open:
                break

        assert not order.is_open, "GTC 挂单应超龄撤销"
        assert account.frozen_cash == 0, "撤销后必须解冻资金"
        assert account.available_cash == pytest.approx(100_000, abs=1e-6)
        assert broker.frozen_summary["expired_by_age"] >= 1

    def test_day_order_expires_same_day(self):
        account, broker = make_broker(100_000)
        account.mark_price("CN:600000", 10.0)
        order = Order(symbol="CN:600000", side=Side.BUY, quantity=1_000,
                      order_type=OrderType.LIMIT, limit_price=9.5, tif=TimeInForce.DAY)
        broker.submit(order)
        broker.on_bar(bar("CN:600000", 2, open_=10.0, high=10.5, low=10.0))
        broker.end_of_day(datetime(2024, 1, 2))
        assert not order.is_open
        assert account.frozen_cash == 0


# --------------------------------------------------------------------------- #
# 不变量 5：除权日不应产生假一字板
# --------------------------------------------------------------------------- #
class TestNoPhantomLimitLock:
    def test_no_limit_without_prev_close(self):
        """没有昨收（首日/数据缺失）时不应凭空判定一字板。"""
        account, broker = make_broker(100_000)
        account.mark_price("CN:600000", 10.0)
        order = Order(symbol="CN:600000", side=Side.BUY, quantity=1_000,
                      order_type=OrderType.MARKET)
        broker.submit(order)
        # prev_close 为 None，limit_up/limit_down 也缺失
        broker.on_bar(Bar(symbol="CN:600000", dt=datetime(2024, 1, 2), open=10.0,
                          high=10.5, low=10.0, close=10.2, volume=1e7, amount=1e8))
        assert order.status.value == "filled", "无昨收数据时不应判为一字板"

    def test_adjust_day_detection(self):
        """价格跳变超过涨跌停限制的日子应被识别为疑似除权日。"""
        from quant.data.base import detect_adjust_days, normalize_frame

        raw = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=4),
            "open": [20.0, 20.0, 10.0, 10.1],
            "high": [20.2, 20.2, 10.2, 10.3],
            "low": [19.8, 19.8, 9.8, 10.0],
            "close": [20.0, 20.0, 10.0, 10.1],
            "volume": [1e6] * 4,
        })
        frame = normalize_frame(raw, "CN:600000")
        days = detect_adjust_days(frame, "CN:600000")
        assert len(days) == 1
        assert days[0]["date"] == "2024-01-03"
        assert days[0]["move_pct"] == pytest.approx(-0.5, abs=1e-6)


# --------------------------------------------------------------------------- #
# 不变量 6：绩效指标的边界
# --------------------------------------------------------------------------- #
class TestMetricInvariants:
    def test_short_series_has_no_absurd_annual(self):
        from quant.metrics.performance import compute

        for n in (1, 2, 3, 4):
            equity = pd.Series(
                [100.0 + i for i in range(n)], index=pd.date_range("2024-01-01", periods=n)
            )
            report = compute(equity, initial_equity=100.0).to_dict()
            assert report["annual_return"] is None, f"{n} 个点不应产出年化收益"

    def test_infinite_profit_factor_is_null(self):
        """只有盈利笔、没有亏损笔时，盈亏比是无穷大，JSON 里必须是 null 而不是 999。"""
        from quant.metrics.performance import compute

        equity = pd.Series([100.0, 110.0], index=pd.date_range("2024-01-01", periods=2))
        # 一笔盈利卖出（没有对应亏损笔 → profit_factor 为无穷）
        trades = [
            Fill(
                order_id="x",
                symbol="CN:600000",
                side=Side.SELL,
                quantity=100,
                price=12.0,
                dt=datetime(2024, 1, 2),
                fees=FeeBreakdown(commission=5.0, stamp_tax=0.0, transfer_fee=0.0, slippage=0.0),
                realized_pnl=150.0,
            )
        ]
        report = compute(equity, trades=trades, initial_equity=100.0).to_dict()
        assert report["profit_factor"] is None
        assert report["win_count"] == 1

    def test_beta_recovers_known_leverage(self):
        """构造 beta 已知的策略，验证估计值收敛。"""
        import numpy as np

        from quant.metrics.performance import compute

        rng = np.random.default_rng(7)
        idx = pd.date_range("2022-01-01", periods=756, freq="B")
        bench_ret = rng.normal(0.0003, 0.012, len(idx))
        bench = pd.Series(100 * np.cumprod(1 + bench_ret), index=idx)
        strat_ret = 1.2 * bench_ret + rng.normal(0, 0.001, len(idx))
        equity = pd.Series(100 * np.cumprod(1 + strat_ret), index=idx)

        report = compute(equity, initial_equity=100.0, benchmark=bench).to_dict()
        assert report["beta"] == pytest.approx(1.2, abs=0.1)
        assert report["excess_return"] is not None
        assert report["tracking_error"] is not None
