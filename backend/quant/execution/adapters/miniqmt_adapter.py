"""miniQMT（迅投 xtquant）A 股实盘适配器 —— 骨架实现。

**运行前提**（缺一不可，缺了会在 ``connect()`` 明确报错）：

1. Windows 本机安装券商 QMT 终端（miniQMT 版），并开通「极简模式」；
2. 券商已为你开通**程序化交易权限**（A 股程序化交易需向券商报备，遵守交易所规定）；
3. 已安装 ``xtquant`` Python 包（随 QMT 终端提供，或 ``pip install xtquant``）。

本仓库的 CI/开发环境没有 QMT 终端，因此这里只保证**接口与状态映射正确**，
真实下单必须在你自己的 Windows 机器上联调验证。系统不绕过券商风控：
所有委托最终都要经过 QMT 与券商端的合规校验。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable

from ...core.markets import parse_instrument
from ...core.types import Order, OrderType, Side
from .base import AdapterBalance, AdapterError, AdapterFill, AdapterStatus, BrokerAdapter

log = logging.getLogger(__name__)

# xtconstant 的状态码（数值是 xtquant 公开常量；用 name 取，缺失时退回数值）
_STATUS_BY_CODE = {
    48: "open",  # 未报
    49: "open",  # 待报
    50: "open",  # 已报
    51: "open",  # 已报待撤
    52: "open",  # 部成待撤
    53: "cancelled",  # 部撤
    54: "cancelled",  # 已撤
    55: "open",  # 部成
    56: "filled",  # 已成
    57: "rejected",  # 废单
}


def _xt_code(symbol: str) -> str:
    """``CN:600519`` → ``600519.SH``（QMT 的股票代码格式）。"""
    inst = parse_instrument(symbol)
    if inst.market.value != "CN":
        raise AdapterError(f"miniQMT 只支持 A 股: {symbol}")
    code = inst.code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


class MiniQmtAdapter(BrokerAdapter):
    name = "miniqmt"
    supports_sandbox = True  # QMT 的模拟账户即 sandbox

    def __init__(
        self,
        *,
        sandbox: bool = True,
        path: str = "",
        account_id: str = "",
        session_id: int = 123456,
        **config: Any,
    ) -> None:
        super().__init__(sandbox=sandbox, **config)
        self.path = path or config.get("qmt_path", "")
        self.account_id = account_id or config.get("account", "")
        self.session_id = int(session_id)
        self._trader: Any = None
        self._account: Any = None
        self._tracked: dict[str, str] = {}  # external_id -> symbol

    # ------------------------------------------------------------------ #
    def _xt(self):
        try:
            from xtquant import xtconstant, xttrader, xttype  # type: ignore[import-not-found]
        except ImportError as exc:
            raise AdapterError(
                "未安装 xtquant：请在本机安装券商 QMT 终端（miniQMT 版）后再接入；"
                "Linux/Docker 环境无法运行 miniQMT"
            ) from exc
        return xtconstant, xttrader, xttype

    def connect(self) -> None:
        if not self.path or not self.account_id:
            raise AdapterError("缺少 QMT 路径或资金账号（请在凭据里填写 path/account_id）")
        _, xttrader, xttype = self._xt()
        try:
            trader = xttrader.XtQuantTrader(self.path, self.session_id)
            trader.start()
            code = trader.connect()
            if code != 0:
                raise AdapterError(f"连接 QMT 失败（返回码 {code}）")
            account = xttype.StockAccount(self.account_id)
            trader.subscribe(account)
        except AdapterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"连接 miniQMT 失败: {exc}") from exc
        self._trader = trader
        self._account = account

    def close(self) -> None:
        trader = self._trader
        if trader is not None:
            try:
                trader.stop()
            except Exception:  # noqa: BLE001
                log.warning("停止 miniQMT 连接时出错", exc_info=True)
        self._trader = None
        self._account = None

    def _client(self):
        if self._trader is None:
            raise AdapterError("适配器未连接，请先调用 connect()")
        return self._trader

    # ------------------------------------------------------------------ #
    def place_order(self, order: Order) -> str:
        xtconstant, _, _ = self._xt()
        trader = self._client()
        side = xtconstant.STOCK_BUY if order.side is Side.BUY else xtconstant.STOCK_SELL
        price_type = xtconstant.FIX_PRICE if order.order_type is OrderType.LIMIT else xtconstant.LATEST_PRICE
        price = float(order.limit_price or 0.0)
        try:
            order_id = trader.order_stock(
                self._account,
                _xt_code(order.symbol),
                side,
                int(round(order.quantity)),
                price_type,
                price,
                order.strategy or "quant",
                order.tag or "",
            )
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"QMT 下单失败: {exc}") from exc
        if order_id is None or int(order_id) <= 0:
            raise AdapterError(f"QMT 拒单（返回 {order_id}）")
        external_id = str(order_id)
        self._tracked[external_id] = order.symbol
        return external_id

    def cancel_order(self, external_id: str, symbol: str) -> bool:
        trader = self._client()
        try:
            code = trader.cancel_order_stock(self._account, int(external_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("QMT 撤单失败 %s: %s", external_id, exc)
            return False
        return code == 0

    # ------------------------------------------------------------------ #
    def fetch_balance(self) -> AdapterBalance:
        trader = self._client()
        try:
            asset = trader.query_stock_asset(self._account)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"查询 QMT 资金失败: {exc}") from exc
        if asset is None:
            raise AdapterError("QMT 未返回资金信息")
        cash = float(getattr(asset, "cash", 0.0) or 0.0)
        total = float(getattr(asset, "total_asset", 0.0) or 0.0)
        return AdapterBalance(cash=cash, equity=total, currency="CNY")

    def fetch_positions(self, symbols: Iterable[str]) -> dict[str, float]:
        trader = self._client()
        try:
            rows = trader.query_stock_positions(self._account) or []
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"查询 QMT 持仓失败: {exc}") from exc
        wanted = {parse_instrument(s).code: parse_instrument(s).id for s in symbols}
        out: dict[str, float] = {}
        for row in rows:
            code = str(getattr(row, "stock_code", "")).split(".")[0]
            volume = float(getattr(row, "volume", 0) or 0)
            if code in wanted and volume > 0:
                out[wanted[code]] = volume
        return out

    def position_detail(self, symbols: Iterable[str]) -> dict[str, dict[str, float]]:
        """持仓明细（成本价/可卖数量），供对账与展示使用。"""
        trader = self._client()
        rows = trader.query_stock_positions(self._account) or []
        wanted = {parse_instrument(s).code: parse_instrument(s).id for s in symbols}
        out: dict[str, dict[str, float]] = {}
        for row in rows:
            code = str(getattr(row, "stock_code", "")).split(".")[0]
            if code not in wanted:
                continue
            out[wanted[code]] = {
                "quantity": float(getattr(row, "volume", 0) or 0),
                "available": float(getattr(row, "can_use_volume", 0) or 0),
                "avg_cost": float(getattr(row, "open_price", 0) or 0),
                "market_value": float(getattr(row, "market_value", 0) or 0),
            }
        return out

    def sync(self) -> list[AdapterFill | AdapterStatus]:
        trader = self._client()
        try:
            rows = trader.query_stock_orders(self._account) or []
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"查询 QMT 委托失败: {exc}") from exc
        events: list[AdapterFill | AdapterStatus] = []
        for row in rows:
            external_id = str(getattr(row, "order_id", "") or "")
            if external_id not in self._tracked:
                continue
            filled = float(getattr(row, "traded_volume", 0) or 0)
            avg = float(getattr(row, "traded_price", 0) or 0)
            if filled > 0:
                events.append(
                    AdapterFill(
                        external_id=external_id,
                        filled=filled,
                        avg_price=avg,
                        fee=0.0,  # QMT 的委托查询不含逐笔费用；日终对账以券商账单为准
                        dt=_order_dt(row),
                    )
                )
            status = _STATUS_BY_CODE.get(int(getattr(row, "order_status", -1) or -1), "open")
            if status != "open":
                events.append(AdapterStatus(external_id=external_id, status=status))
                self._tracked.pop(external_id, None)
        return events


def _order_dt(row: Any) -> datetime | None:
    raw = getattr(row, "order_time", None)
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw)) if str(raw).isdigit() else None
    except (OSError, ValueError):
        return None


__all__ = ["MiniQmtAdapter"]
