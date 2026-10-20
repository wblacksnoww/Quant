"""风控规则：仓位、黑名单、整手、频率与熔断。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from quant.core.portfolio import Account
from quant.core.types import FeeBreakdown, Fill, Order, Side
from quant.risk.base import RiskManager


def state_for(manager: RiskManager, account: Account, now: datetime | None = None):
    return manager.build_state(account, now or datetime(2024, 1, 2, 9, 30, tzinfo=UTC))


def test_position_limit_blocks_oversized_buy():
    account = Account(100_000)
    manager = RiskManager.from_config([{"id": "max_position_pct", "limit": 0.10}])
    order = Order("600519", Side.BUY, 1_000, limit_price=20.0)  # 2 万 / 10 万 = 20%

    decision = manager.pre_trade(order, account, now=datetime(2024, 1, 2, 9, 30))
    assert not decision.allowed
    assert decision.rule == "max_position_pct"
    assert decision.severity == "warning"


def test_blacklist_blocks_symbol():
    account = Account(100_000)
    manager = RiskManager.from_config([{"id": "blacklist", "symbols": ["600519"]}])
    decision = manager.pre_trade(Order("600519", Side.BUY, 100), account)
    assert not decision.allowed
    assert decision.rule == "blacklist"


def test_lot_size_rejects_odd_lot_buy():
    account = Account(100_000)
    manager = RiskManager.from_config([{"id": "lot_size"}])
    decision = manager.pre_trade(Order("600519", Side.BUY, 150), account)
    assert not decision.allowed
    assert decision.rule == "lot_size"


def test_order_rate_limit_uses_market_clock():
    account = Account(100_000)
    manager = RiskManager.from_config([{"id": "order_rate_limit", "max_orders": 2, "window_seconds": 60}])
    base = datetime(2024, 1, 2, 9, 30)
    assert manager.pre_trade(Order("600519", Side.BUY, 100), account, now=base).allowed
    assert manager.pre_trade(
        Order("600519", Side.BUY, 100), account, now=base + timedelta(seconds=10)
    ).allowed
    blocked = manager.pre_trade(Order("600519", Side.BUY, 100), account, now=base + timedelta(seconds=20))
    assert not blocked.allowed
    assert blocked.rule == "order_rate_limit"

    # 窗口滑过后恢复
    assert manager.pre_trade(
        Order("600519", Side.BUY, 100), account, now=base + timedelta(seconds=90)
    ).allowed


def test_drawdown_circuit_breaker_blocks_buys_not_sells():
    account = Account(100_000)
    account.mark_price("000001", 10.0)
    account.apply_fill(
        Fill(
            order_id="o",
            symbol="000001",
            side=Side.BUY,
            quantity=5_000,
            price=10.0,
            dt=datetime(2024, 1, 2),
            fees=FeeBreakdown(),
        )
    )
    manager = RiskManager.from_config([{"id": "max_drawdown", "limit": 0.10}])
    first = datetime(2024, 1, 2, 9, 30)
    assert manager.pre_trade(Order("600519", Side.BUY, 100), account, now=first).allowed

    # 权益从 10 万跌到 8 万，回撤 20%
    account.mark_price("000001", 6.0)
    decision = manager.pre_trade(Order("600519", Side.BUY, 100), account, now=first + timedelta(days=1))
    assert not decision.allowed
    assert decision.rule == "max_drawdown"
    # 卖出（减仓）不受回撤熔断限制
    assert manager.pre_trade(Order("000001", Side.SELL, 100), account, now=first + timedelta(days=1)).allowed


def test_presets_reference_known_rules():
    from quant.risk.base import RULE_TYPES

    manager = RiskManager.from_config(None)
    ids = {rule.rule_id for rule in manager.rules}
    assert ids <= set(RULE_TYPES)
    assert {"max_position_pct", "max_drawdown", "blacklist"} <= ids


def test_empty_config_disables_all_rules():
    """ "risk=[] 显式空列表 = 无风控；None = 默认规则集。CLI `--no-risk` 依赖此语义。"""
    account = Account(100_000)
    manager = RiskManager.from_config([])
    assert manager.rules == []
    assert manager.pre_trade(Order("600519", Side.BUY, 100), account).allowed
