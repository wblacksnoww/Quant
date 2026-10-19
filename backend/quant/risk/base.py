"""风控层：下单前规则链。

每条规则独立、可插拔、可通过配置启停与调参。风控在 **Broker 之前**执行：
被拒的订单不会冻结资金，也不会进入撮合队列。

规则实现约定：

* :meth:`RiskRule.check` 只读，不做副作用，便于并行与单测。
* 需要跨订单状态的规则（如频率限制）在 :meth:`RiskRule.observe` 里累积。
* 规则抛异常视为"拒绝"，宁可少交易，不可失控。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from ..core.events import EventBus, EventType
from ..core.portfolio import Account
from ..core.types import AccountSnapshot, Fill, Order, Side, utcnow
from ..execution.rules import round_down_lot, rules_for

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool = True
    rule: str = ""
    reason: str = ""
    severity: str = "info"  # info | warning | critical

    @classmethod
    def ok(cls) -> RiskDecision:
        return cls(allowed=True)

    @classmethod
    def block(cls, rule: str, reason: str, severity: str = "warning") -> RiskDecision:
        return cls(allowed=False, rule=rule, reason=reason, severity=severity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "rule": self.rule,
            "reason": self.reason,
            "severity": self.severity,
        }


@dataclass
class RiskState:
    """规则共享的只读状态快照。"""

    account: Account
    equity: float
    peak_equity: float
    day_start_equity: float
    today: date
    now: datetime
    open_order_count: int = 0
    position_count: int = 0

    @property
    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return 1.0 - self.equity / self.peak_equity

    @property
    def daily_pnl_pct(self) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return self.equity / self.day_start_equity - 1.0


class RiskRule(ABC):
    """风控规则基类。"""

    rule_id: str = ""
    display_name: str = ""
    description: str = ""
    enabled: bool = True
    severity: str = "warning"

    def __init__(self, **params: Any) -> None:
        self.enabled = bool(params.pop("enabled", True))
        self.params = params
        self.trigger_count = 0

    @abstractmethod
    def check(self, order: Order, state: RiskState) -> RiskDecision:
        """下单前校验。"""

    def observe(self, fill: Fill, state: RiskState) -> None:  # noqa: B027
        """每笔成交后调用，供频率类规则累计状态。"""

    def on_new_day(self, state: RiskState) -> None:  # noqa: B027
        """交易日切换时重置日内状态。"""

    def schema(self) -> dict[str, Any]:
        return {
            "id": self.rule_id,
            "name": self.display_name,
            "description": self.description,
            "enabled": self.enabled,
            "severity": self.severity,
            "params": self.params,
            "trigger_count": self.trigger_count,
        }


# --------------------------------------------------------------------------- #
# 具体规则
# --------------------------------------------------------------------------- #


class MaxPositionPct(RiskRule):
    rule_id = "max_position_pct"
    display_name = "单票仓位上限"
    description = "单只标的市值不得超过总权益的指定比例。"

    def check(self, order: Order, state: RiskState) -> RiskDecision:
        if order.side is Side.SELL or state.equity <= 0:
            return RiskDecision.ok()
        limit = float(self.params.get("limit", 0.30))
        price = float(order.limit_price or state.account.last_price(order.symbol) or 0.0)
        if price <= 0:
            return RiskDecision.ok()
        pos = state.account.get_position(order.symbol)
        projected = (pos.market_value + price * order.quantity) / state.equity
        if projected > limit + 1e-9:
            return self.block(
                f"{order.symbol} 目标仓位 {projected:.1%} 超过上限 {limit:.1%}",
            )
        return RiskDecision.ok()

    def block(self, reason: str) -> RiskDecision:
        self.trigger_count += 1
        return RiskDecision.block(self.rule_id, reason, self.severity)


class MaxOrderValuePct(RiskRule):
    rule_id = "max_order_value_pct"
    display_name = "单笔委托金额上限"
    description = "单笔委托金额不得超过总权益的指定比例，防止手滑梭哈。"

    def check(self, order: Order, state: RiskState) -> RiskDecision:
        if state.equity <= 0:
            return RiskDecision.ok()
        limit = float(self.params.get("limit", 0.20))
        price = float(order.limit_price or state.account.last_price(order.symbol) or 0.0)
        if price <= 0:
            return RiskDecision.ok()
        value = price * order.quantity
        if value / state.equity > limit + 1e-9:
            self.trigger_count += 1
            return RiskDecision.block(
                self.rule_id,
                f"单笔金额 {value:.0f} 元占权益 {value / state.equity:.1%}，超过 {limit:.1%}",
                self.severity,
            )
        return RiskDecision.ok()


class MaxPositions(RiskRule):
    rule_id = "max_positions"
    display_name = "最大持仓数量"
    description = "同时持有的标的数量上限，避免过度分散导致管理成本上升。"

    def check(self, order: Order, state: RiskState) -> RiskDecision:
        if order.side is Side.SELL:
            return RiskDecision.ok()
        limit = int(self.params.get("limit", 10))
        pos = state.account.get_position(order.symbol)
        opening = pos.is_flat
        if opening and state.position_count >= limit:
            self.trigger_count += 1
            return RiskDecision.block(
                self.rule_id,
                f"已持有 {state.position_count} 只，达到上限 {limit}",
                self.severity,
            )
        return RiskDecision.ok()


class DailyLossLimit(RiskRule):
    rule_id = "daily_loss_limit"
    display_name = "日内亏损熔断"
    description = "当日亏损超过阈值后禁止开新仓（只允许减仓）。"

    def check(self, order: Order, state: RiskState) -> RiskDecision:
        if order.side is Side.SELL:
            return RiskDecision.ok()
        limit = float(self.params.get("limit", 0.05))
        if state.daily_pnl_pct <= -limit:
            self.trigger_count += 1
            return RiskDecision.block(
                self.rule_id,
                f"当日亏损 {state.daily_pnl_pct:.2%} 触及熔断线 {-limit:.2%}，暂停开仓",
                "critical",
            )
        return RiskDecision.ok()


class MaxDrawdown(RiskRule):
    rule_id = "max_drawdown"
    display_name = "最大回撤熔断"
    description = "从权益峰值回撤超过阈值后，拒绝一切买入指令。"

    def check(self, order: Order, state: RiskState) -> RiskDecision:
        if order.side is Side.SELL:
            return RiskDecision.ok()
        limit = float(self.params.get("limit", 0.20))
        if state.drawdown >= limit:
            self.trigger_count += 1
            return RiskDecision.block(
                self.rule_id,
                f"当前回撤 {state.drawdown:.2%} 超过阈值 {limit:.2%}，停止开仓",
                "critical",
            )
        return RiskDecision.ok()


class OrderRateLimit(RiskRule):
    rule_id = "order_rate_limit"
    display_name = "下单频率限制"
    description = "单位时间内最多提交 N 笔订单，防止策略死循环刷单。"

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self._timestamps: deque[float] = deque()

    def check(self, order: Order, state: RiskState) -> RiskDecision:
        window = float(self.params.get("window_seconds", 60))
        limit = int(self.params.get("max_orders", 30))
        # 用行情时钟而非墙钟：回测在几秒内推进几年，墙钟会让频率限制完全失真
        now = state.now.timestamp()
        while self._timestamps and now - self._timestamps[0] > window:
            self._timestamps.popleft()
        if len(self._timestamps) >= limit:
            self.trigger_count += 1
            return RiskDecision.block(
                self.rule_id,
                f"{window:.0f} 秒内已下单 {len(self._timestamps)} 次，超过上限 {limit}",
                self.severity,
            )
        self._timestamps.append(now)
        return RiskDecision.ok()


class Blacklist(RiskRule):
    rule_id = "blacklist"
    display_name = "标的黑名单"
    description = "禁止交易指定标的（ST、退市整理期、问题股等）。"

    def check(self, order: Order, state: RiskState) -> RiskDecision:
        symbols = set(self.params.get("symbols", []) or [])
        if order.symbol in symbols:
            self.trigger_count += 1
            return RiskDecision.block(self.rule_id, f"{order.symbol} 在黑名单中", self.severity)
        return RiskDecision.ok()


class LotSizeCheck(RiskRule):
    rule_id = "lot_size"
    display_name = "整手校验"
    description = "买入数量必须是整手（主板 100 股 / 科创板 200 股）。"

    def check(self, order: Order, state: RiskState) -> RiskDecision:
        if order.side is Side.SELL:
            return RiskDecision.ok()
        lot = rules_for(order.symbol).lot_size
        if lot > 1 and abs(order.quantity - round_down_lot(order.quantity, order.symbol)) > 1e-9:
            self.trigger_count += 1
            return RiskDecision.block(
                self.rule_id, f"买入数量 {order.quantity} 不是 {lot} 的整数倍", self.severity
            )
        return RiskDecision.ok()


RULE_TYPES: dict[str, type[RiskRule]] = {
    cls.rule_id: cls
    for cls in (MaxPositionPct, MaxOrderValuePct, MaxPositions, DailyLossLimit, MaxDrawdown, OrderRateLimit, Blacklist, LotSizeCheck)
}


# --------------------------------------------------------------------------- #
# 管理器
# --------------------------------------------------------------------------- #


DEFAULT_RULES: list[dict[str, Any]] = [
    {"id": "lot_size", "enabled": True},
    # 仓位类上限默认给到 98%：它的职责是拦住"策略写错导致下单超过权益"这类事故，
    # 而不是替用户决定仓位——仓位是策略参数的事。需要更严的约束时在界面上调小即可。
    {"id": "max_position_pct", "enabled": True, "limit": 0.98},
    {"id": "max_order_value_pct", "enabled": True, "limit": 0.98},
    {"id": "max_positions", "enabled": True, "limit": 20},
    {"id": "daily_loss_limit", "enabled": True, "limit": 0.10},
    {"id": "max_drawdown", "enabled": True, "limit": 0.30},
    {"id": "order_rate_limit", "enabled": True, "max_orders": 120, "window_seconds": 60},
    {"id": "blacklist", "enabled": True, "symbols": []},
]


class RiskManager:
    """规则链执行器。"""

    def __init__(
        self,
        rules: Iterable[RiskRule] | None = None,
        bus: EventBus | None = None,
        *,
        stop_on_critical: bool = True,
    ) -> None:
        self.rules: list[RiskRule] = list(rules or [])
        self.bus = bus or EventBus("risk")
        self.stop_on_critical = stop_on_critical
        self.peak_equity = 0.0
        self.day_start_equity = 0.0
        self._today: date | None = None
        self.rejections = 0
        self.last_decision: RiskDecision = RiskDecision.ok()

    # ------------------------------------------------------------------ #
    @classmethod
    def from_config(cls, config: Iterable[dict[str, Any]] | None, **kwargs: Any) -> RiskManager:
        """用 ``[{id, enabled, ...params}]`` 构造。未知 id 会被忽略并告警。"""
        entries = list(config) if config is not None else list(DEFAULT_RULES)
        rules: list[RiskRule] = []
        for entry in entries:
            rule_id = str(entry.get("id", ""))
            cls_ = RULE_TYPES.get(rule_id)
            if cls_ is None:
                log.warning("未知风控规则 %s，已忽略", rule_id)
                continue
            params = {k: v for k, v in entry.items() if k != "id"}
            rules.append(cls_(**params))
        return cls(rules, **kwargs)

    # ------------------------------------------------------------------ #
    def build_state(self, account: Account, now: datetime | None = None, open_orders: int = 0) -> RiskState:
        equity = account.equity()
        moment = now or utcnow()
        today = moment.date()
        if self._today != today:
            self._today = today
            self.day_start_equity = equity
            state = RiskState(
                account=account,
                equity=equity,
                peak_equity=self.peak_equity or equity,
                day_start_equity=equity,
                today=today,
                now=moment,
                open_order_count=open_orders,
                position_count=len(account.holding_symbols()),
            )
            for rule in self.rules:
                rule.on_new_day(state)
        self.peak_equity = max(self.peak_equity, equity)
        if self.day_start_equity <= 0:
            self.day_start_equity = equity
        return RiskState(
            account=account,
            equity=equity,
            peak_equity=self.peak_equity or equity,
            day_start_equity=self.day_start_equity or equity,
            today=today,
            now=moment,
            open_order_count=open_orders,
            position_count=len(account.holding_symbols()),
        )

    # ------------------------------------------------------------------ #
    def pre_trade(self, order: Order, account: Account, now: datetime | None = None, open_orders: int = 0) -> RiskDecision:
        """依次执行所有启用规则，任一拒绝即返回。"""
        state = self.build_state(account, now, open_orders)
        for rule in self.rules:
            if not rule.enabled:
                continue
            try:
                decision = rule.check(order, state)
            except Exception as exc:  # noqa: BLE001 - 规则故障按拒绝处理
                log.exception("风控规则 %s 异常", rule.rule_id)
                decision = RiskDecision.block(rule.rule_id, f"风控规则异常: {exc}", "critical")
            if not decision.allowed:
                self.rejections += 1
                self.last_decision = decision
                self.bus.emit(
                    EventType.RISK,
                    {"order": order.to_dict(), **decision.to_dict()},
                    source="risk",
                )
                log.info("风控拒单 %s %s %s: %s", order.symbol, order.side.value, order.quantity, decision.reason)
                return decision
        self.last_decision = RiskDecision.ok()
        return self.last_decision

    def observe_fill(self, fill: Fill, account: Account, now: datetime | None = None) -> None:
        state = self.build_state(account, now or fill.dt)
        for rule in self.rules:
            if rule.enabled:
                try:
                    rule.observe(fill, state)
                except Exception:  # noqa: BLE001
                    log.exception("风控规则 %s observe 异常", rule.rule_id)

    # ------------------------------------------------------------------ #
    def schema(self) -> list[dict[str, Any]]:
        return [rule.schema() for rule in self.rules]

    def add(self, rule: RiskRule) -> None:
        self.rules.append(rule)

    def remove(self, rule_id: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.rule_id != rule_id]
        return len(self.rules) < before

    def reset(self) -> None:
        self.peak_equity = 0.0
        self.day_start_equity = 0.0
        self._today = None
        self.rejections = 0
        for rule in self.rules:
            rule.trigger_count = 0


__all__ = [
    "DEFAULT_RULES",
    "RULE_TYPES",
    "Blacklist",
    "DailyLossLimit",
    "LotSizeCheck",
    "MaxDrawdown",
    "MaxOrderValuePct",
    "MaxPositionPct",
    "MaxPositions",
    "OrderRateLimit",
    "RiskDecision",
    "RiskManager",
    "RiskRule",
    "RiskState",
]
