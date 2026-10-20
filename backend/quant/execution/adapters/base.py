"""实盘券商适配器协议。

职责边界：**适配器只做"翻译"**——把券商的同步接口翻译成统一的下单/撤单/查询/回报，
不持有账户记账（那是 :class:`~quant.core.portfolio.Account` 的事），也不做风控
（那是 :class:`~quant.risk.base.RiskManager` 的事）。

回报采用**累计口径**（``filled`` 是交易所侧的累计成交量、``avg_price`` 是累计均价），
由 :class:`~quant.execution.live_broker.LiveBroker` 计算增量，重复 ``sync()`` 不会重复记账。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ...core.types import Order


class AdapterError(Exception):
    """适配器层错误（未连接、下单被交易所拒绝、网络失败等）。"""


@dataclass(frozen=True, slots=True)
class AdapterFill:
    """交易所侧累计成交快照。"""

    external_id: str
    filled: float  # 累计成交量
    avg_price: float  # 累计成交均价
    fee: float = 0.0  # 累计费用
    dt: datetime | None = None


@dataclass(frozen=True, slots=True)
class AdapterStatus:
    """交易所侧订单状态（open/filled/cancelled/rejected/expired）。"""

    external_id: str
    status: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AdapterBalance:
    cash: float
    equity: float
    currency: str = ""


class BrokerAdapter(ABC):
    """券商/交易所适配器统一接口。

    实现者至少要覆盖：``connect`` / ``close`` / ``place_order`` / ``cancel_order`` /
    ``fetch_balance`` / ``fetch_positions`` / ``sync``。``quote`` 可选（不支持时由
    上层退回行情数据源）。
    """

    name: str = "base"
    supports_sandbox: bool = False
    requires_api_key: bool = True

    def __init__(self, *, sandbox: bool = False, **config: Any) -> None:
        self.sandbox = bool(sandbox)
        self.config = config

    # -- 生命周期 ---------------------------------------------------------- #
    @abstractmethod
    def connect(self) -> None:
        """建立连接/校验凭据；失败抛 :class:`AdapterError`。"""

    @abstractmethod
    def close(self) -> None:
        """释放连接（幂等）。"""

    # -- 交易 -------------------------------------------------------------- #
    @abstractmethod
    def place_order(self, order: Order) -> str:
        """下单，返回交易所委托号（``external_id``）。"""

    @abstractmethod
    def cancel_order(self, external_id: str, symbol: str) -> bool:
        """撤单，成功/已终态返回 True。"""

    # -- 查询 -------------------------------------------------------------- #
    @abstractmethod
    def fetch_balance(self) -> AdapterBalance:
        """账户资金（以计价货币计）。"""

    @abstractmethod
    def fetch_positions(self, symbols: Iterable[str]) -> dict[str, float]:
        """持仓数量（``symbol -> quantity``），只返回非零项。"""

    @abstractmethod
    def sync(self) -> list[AdapterFill | AdapterStatus]:
        """拉取本地已知订单的最新状态与累计成交。"""

    def quote(self, symbols: Iterable[str]) -> dict[str, float]:
        """最新价；不支持时抛 ``NotImplementedError``，由引擎回退行情源。"""
        raise NotImplementedError(f"{self.name} 不提供实时报价")

    def __repr__(self) -> str:
        mode = "sandbox" if self.sandbox else "live"
        return f"<{type(self).__name__} {self.name} {mode}>"


__all__ = [
    "AdapterBalance",
    "AdapterError",
    "AdapterFill",
    "AdapterStatus",
    "BrokerAdapter",
]
