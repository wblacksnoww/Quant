"""做空（direction=short）与负持仓结算。

守的口径：

1. 权益 = 现金 + 市值对空头自动成立——开空所得入现金、空头市值为负，
   不需要独立保证金账户；这条不成立，所有空头权益曲线都是假的。
2. 现金永不为负：买回平空现金不足时拒单/缩减，绝不静默穿仓。
3. 市场资格：A 股现货不允许做空（卖出量不得超过可卖数量），
   虚拟货币/美股/港股允许；A 股与场外基金传 direction=short 会被引擎拒绝。
4. 已实现盈亏在平空（买入）时也会产生——「买入恒为 0」是旧口径。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from quant.core.errors import InsufficientMargin, ShortNotAllowed
from quant.core.portfolio import Account
from quant.core.types import FeeBreakdown, Fill, Side
from quant.engine import BacktestEngine, EngineConfig


def _crypto_fill(side: Side, qty: float, price: float, fee: float = 0.0) -> Fill:
    return Fill(
        "ord_test",
        "CRYPTO:BTC/USDT",
        side,
        qty,
        price,
        datetime(2024, 1, 1),
        FeeBreakdown(commission=fee),
    )


# --------------------------------------------------------------------------- #
# Account 四象限结算
# --------------------------------------------------------------------------- #


def test_open_short_adds_proceeds_to_cash():
    a = Account(100_000)
    realized = a.apply_fill(_crypto_fill(Side.SELL, 1.0, 40_000, fee=10))
    assert realized == 0.0  # 纯开仓没有已实现盈亏
    pos = a.get_position("CRYPTO:BTC/USDT")
    assert pos.quantity == -1.0
    assert pos.direction == "short"
    assert a.cash == pytest.approx(100_000 + 40_000 - 10)
    # 空头成本 = 扣费后净卖出所得：40_000 - 10 = 39_990
    assert pos.avg_cost == pytest.approx(39_990)


def test_short_profits_when_price_falls():
    a = Account(100_000)
    a.apply_fill(_crypto_fill(Side.SELL, 1.0, 40_000, fee=10))
    pos = a.get_position("CRYPTO:BTC/USDT")
    pos.last_price = 38_000
    # equity = cash + market_value = 139_990 + (-38_000) = 101_990 → 盈利 1_990
    assert a.equity() == pytest.approx(101_990)
    # 成本基准 39_990（净所得），空头未实现 = (39_990 - 38_000) * 1
    assert pos.unrealized_pnl == pytest.approx(1_990)
    # 分母取绝对值：空头未实现收益率符号必须正确（这里是正的）
    assert pos.unrealized_pct > 0
    assert pos.unrealized_pct == pytest.approx(1_990 / 39_990)


def test_close_short_realizes_pnl_on_buy():
    a = Account(100_000)
    a.apply_fill(_crypto_fill(Side.SELL, 1.0, 40_000, fee=10))
    realized = a.apply_fill(_crypto_fill(Side.BUY, 1.0, 38_000, fee=8))
    # (39_990 - 38_000) * 1 - 8 = 1_982；买入也产生已实现盈亏
    assert realized == pytest.approx(1_982)
    assert a.get_position("CRYPTO:BTC/USDT").is_flat
    assert a.realized_pnl == pytest.approx(1_982)
    assert a.cash == pytest.approx(139_990 - 38_000 - 8)
    assert a.equity() == pytest.approx(a.cash)


def test_flip_through_zero_long_to_short():
    a = Account(100_000)
    a.apply_fill(_crypto_fill(Side.BUY, 0.5, 40_000, fee=5))
    realized = a.apply_fill(_crypto_fill(Side.SELL, 1.5, 42_000, fee=9))
    pos = a.get_position("CRYPTO:BTC/USDT")
    # 开多含费成本 = 40_010（5 元费摊 0.5 份）；平多段已实现：(42_000 - 40_010) * 0.5 - 9/3
    assert realized == pytest.approx((42_000 - 40_010) * 0.5 - 3.0)
    assert pos.quantity == pytest.approx(-1.0)
    assert pos.direction == "short"
    # 权益自洽：initial + 已实现 + 未实现（开空瞬间未实现 = -开仓费）
    assert a.equity() == pytest.approx(100_000 + realized + pos.unrealized_pnl)


def test_short_margin_rejects_beyond_equity():
    a = Account(100_000)
    a.apply_fill(_crypto_fill(Side.SELL, 2.0, 40_000))  # 名义 80_000 ≤ 100_000
    with pytest.raises(InsufficientMargin):
        a.apply_fill(_crypto_fill(Side.SELL, 1.0, 40_000))  # 名义将达 120_000 > 权益
    # 价格上涨使权益缩水后，加空也被拒
    a.get_position(
        "CRYPTO:BTC/USDT"
    ).last_price = 55_000  # equity = 180_000-110_000 = 70_000... 2*40k卖出=180k cash，mv=-110k
    with pytest.raises(InsufficientMargin):
        a.apply_fill(_crypto_fill(Side.SELL, 1.0, 55_000))


def test_stock_market_rejects_short():
    """A 股现货不允许负持仓：卖出超出可卖数量直接拒绝。"""
    a = Account(100_000)
    with pytest.raises(ShortNotAllowed):
        a.apply_fill(
            Fill(
                "ord_cn",
                "600519",
                Side.SELL,
                100,
                10.0,
                datetime(2024, 1, 1),
                FeeBreakdown(),
            )
        )


def test_fund_market_rejects_short():
    a = Account(100_000)
    with pytest.raises(ShortNotAllowed):
        a.apply_fill(
            Fill(
                "ord_fund",
                "FUND:007005",
                Side.SELL,
                10,
                1.0,
                datetime(2024, 1, 1),
                FeeBreakdown(),
            )
        )


@pytest.mark.parametrize("symbol", ["US:AAPL", "HK:00700"])
def test_shortable_markets_accept_negative_position(symbol):
    """美股/港股在账本层同样接受负持仓——资格不能只在配置校验里放行。"""
    a = Account(100_000)
    a.apply_fill(Fill("ord_x", symbol, Side.SELL, 10, 100.0, datetime(2024, 1, 1), FeeBreakdown()))
    pos = a.get_position(symbol)
    assert pos.quantity == pytest.approx(-10)
    assert pos.is_short
    assert a.equity() == pytest.approx(100_000)  # 开空瞬间权益不变（无费用）


# --------------------------------------------------------------------------- #
# 引擎端到端：direction=short
# --------------------------------------------------------------------------- #


def _engine_config(direction: str = "long", **params) -> EngineConfig:
    return EngineConfig(
        strategy_id="dual_ma",
        symbols=["CRYPTO:BTC/USDT"],
        start=date(2023, 1, 1),
        end=date(2024, 6, 30),
        initial_cash=1_000_000,
        direction=direction,
        params=params,
    )


def _config_for(symbols: list[str], direction: str = "long") -> EngineConfig:
    return EngineConfig(
        strategy_id="dual_ma",
        symbols=symbols,
        start=date(2023, 1, 1),
        end=date(2024, 6, 30),
        direction=direction,
    )


@pytest.mark.parametrize("symbols", [["CN:600519"], ["FUND:007005"]])
def test_direction_short_rejects_markets_without_short(symbols):
    """不可做空的市场必须拒绝 direction=short，且错误信息说清原因。"""
    with pytest.raises(ValueError, match="不支持做空"):
        _config_for(symbols, "short").validate()


@pytest.mark.parametrize("symbols", [["CRYPTO:BTC/USDT"], ["US:AAPL"], ["HK:00700"]])
def test_direction_short_allowed_on_shortable_markets(symbols):
    """虚拟货币 / 美股 / 港股允许做空——资格由市场能力表决定，不是硬编码。"""
    _config_for(symbols, "short").validate()


def test_short_backtest_trades_both_sides(context):
    """direction=short 的空头回测：有成交、权益曲线存在。

    注意不要断言"权益恒为正"：v1 保证金模型是 1x 无杠杆、不逐 bar 盯市，
    空头浮亏可以把权益打穿零（文档写明的限制，不是记账错误）。
    同一份确定性数据在不同编译器的浮点路径下，信号可能在边缘翻转，
    断言曲线形状会让测试依赖具体执行路径。
    """
    result = BacktestEngine(_engine_config("short", fast=5, slow=20), context.data).run()
    assert result.status == "finished", result.error
    assert result.metrics.trade_count > 0, "空头策略应有成交"
    assert result.equity_curve, "应有权益曲线"


def test_short_and_long_differ_on_same_data(context):
    """同一份数据，多空两个方向的回测结果必须不同（方向翻转真的生效）。"""
    long_result = BacktestEngine(_engine_config("long", fast=5, slow=20), context.data).run()
    short_result = BacktestEngine(_engine_config("short", fast=5, slow=20), context.data).run()
    assert long_result.status == "finished"
    assert short_result.status == "finished"
    short_sides = {t["side"] for t in short_result.trades}
    assert short_sides, "空头回测应有成交"
    # 空头策略的第一笔开仓是卖出（多头是买入）
    assert short_result.trades[0]["side"] == "sell"
    assert long_result.trades[0]["side"] == "buy"
    # 两个方向的总收益不该完全相同（数据非对称；若完全相等说明翻转没生效）
    assert long_result.metrics.total_return != pytest.approx(short_result.metrics.total_return)


def test_native_short_backtest_matches_python_direction(context):
    """C++ 核的 direction=short 与 Python 路径语义一致：首笔开仓是卖出。

    C++ 引擎是默认回测通道，如果只有 Python 侧支持做空而 C++ 忽略
    direction，用户会看到「选了做空还是做多」——比报错更危险。
    """
    import quant.core_native as core_native

    if not core_native.HAS_NATIVE:
        pytest.skip("quant._core 不可用")
    cfg = _engine_config("short", fast=5, slow=20)
    result = BacktestEngine(cfg, context.data).run()
    assert result.status == "finished", result.error
    assert result.metrics.trade_count > 0
    assert result.trades[0]["side"] == "sell", "空头回测首笔开仓必须是卖出"


@pytest.mark.parametrize("symbol", ["US:AAPL", "HK:00700"])
def test_native_stock_markets_short_opens_short(symbol, tmp_path):
    """美股/港股做空在 C++ 核同样生效。

    C++ 的 SymbolRules.can_short 若仍只认 CRYPTO，开空成交会被账本拒绝，
    用户看到的是「选了做空却没有成交」——比报错更难发现。
    """
    import quant.core_native as core_native

    if not core_native.HAS_NATIVE:
        pytest.skip("quant._core 不可用")

    from quant.data.providers.synthetic import SyntheticProvider
    from quant.data.store import DataStore, MarketDataService

    data = MarketDataService(DataStore(root=tmp_path / "store", provider=SyntheticProvider()))
    cfg = EngineConfig(
        strategy_id="dual_ma",
        symbols=[symbol],
        start=date(2024, 1, 1),
        end=date(2024, 6, 30),
        initial_cash=1_000_000,
        direction="short",
        params={"fast": 5, "slow": 20},
    )
    result = BacktestEngine(cfg, data).run()
    assert result.status == "finished", result.error
    assert result.trades, "空头回测应有成交"
    assert result.trades[0]["side"] == "sell", "首笔开仓必须是卖出"
