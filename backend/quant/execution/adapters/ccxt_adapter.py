"""ccxt 实盘适配器（虚拟货币，现货）。

* 交易所可配置（``okx`` / ``binance`` / ``gate`` …），默认 ``QUANT_CRYPTO_EXCHANGE``；
* ``sandbox=True`` 时走交易所测试网（OKX/Binance 均支持 ``set_sandbox_mode``）；
* 只支持**现货**：合约/杠杆的持仓口径与保证金结算差异太大，不在此实现。

适配器只做翻译，累计口径见 :mod:`quant.execution.adapters.base`。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from ...core.markets import parse_instrument
from ...core.types import Order, OrderType, Side
from .base import AdapterBalance, AdapterError, AdapterFill, AdapterStatus, BrokerAdapter

log = logging.getLogger(__name__)

_STATUS_MAP = {
    "open": "open",
    "closed": "filled",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "rejected": "rejected",
    "expired": "expired",
}


class CcxtAdapter(BrokerAdapter):
    name = "ccxt"
    supports_sandbox = True

    def __init__(
        self,
        *,
        sandbox: bool = False,
        exchange: str | None = None,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        proxy: str | None = None,
        quote_currency: str = "USDT",
        **config: Any,
    ) -> None:
        super().__init__(sandbox=sandbox, **config)
        self.exchange_id = (exchange or os.environ.get("QUANT_CRYPTO_EXCHANGE") or "okx").strip().lower()
        self.api_key = api_key or os.environ.get("QUANT_CRYPTO_API_KEY", "")
        self.secret = secret or os.environ.get("QUANT_CRYPTO_SECRET", "")
        self.password = password or os.environ.get("QUANT_CRYPTO_PASSWORD", "")
        self.proxy = proxy or os.environ.get("QUANT_CRYPTO_PROXY", "") or ""
        self.quote_currency = quote_currency.upper()
        self._ex: Any = None
        self._tracked: dict[str, str] = {}  # external_id -> pair

    # ------------------------------------------------------------------ #
    def _ccxt(self):
        try:
            import ccxt  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise AdapterError("未安装 ccxt：pip install ccxt") from exc
        return ccxt

    def connect(self) -> None:
        if not self.api_key or not self.secret:
            raise AdapterError("缺少 API Key/Secret（请在「设置 → 凭据」里创建 broker 凭据）")
        ccxt = self._ccxt()
        if not hasattr(ccxt, self.exchange_id):
            raise AdapterError(f"ccxt 不支持的交易所: {self.exchange_id}")
        config: dict[str, Any] = {
            "apiKey": self.api_key,
            "secret": self.secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
        if self.password:
            config["password"] = self.password
        if self.proxy:
            config["proxies"] = {"http": self.proxy, "https": self.proxy}
        ex = getattr(ccxt, self.exchange_id)(config)
        if self.sandbox:
            try:
                ex.set_sandbox_mode(True)
            except Exception as exc:  # noqa: BLE001
                raise AdapterError(f"{self.exchange_id} 不支持测试网: {exc}") from exc
        try:
            ex.load_markets()
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"连接 {self.exchange_id} 失败: {exc}") from exc
        self._ex = ex

    def close(self) -> None:
        self._ex = None

    def _exchange(self):
        if self._ex is None:
            raise AdapterError("适配器未连接，请先调用 connect()")
        return self._ex

    # ------------------------------------------------------------------ #
    def _pair(self, symbol: str) -> str:
        inst = parse_instrument(symbol)
        if inst.market.value != "CRYPTO":
            raise AdapterError(f"ccxt 适配器只接受 CRYPTO: 标的: {symbol}")
        return inst.code

    def place_order(self, order: Order) -> str:
        ex = self._exchange()
        pair = self._pair(order.symbol)
        side = "buy" if order.side is Side.BUY else "sell"
        order_type = "market" if order.order_type is OrderType.MARKET else "limit"
        price = float(order.limit_price) if order.limit_price else None
        try:
            created = ex.create_order(pair, order_type, side, float(order.quantity), price)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(str(exc)) from exc
        external_id = str(created.get("id") or "")
        if not external_id:
            raise AdapterError(f"下单返回缺少委托号: {created}")
        self._tracked[external_id] = pair
        return external_id

    def cancel_order(self, external_id: str, symbol: str) -> bool:
        ex = self._exchange()
        pair = self._tracked.get(external_id) or self._pair(symbol)
        try:
            ex.cancel_order(external_id, pair)
            return True
        except Exception as exc:  # noqa: BLE001 - 已成交/已撤单也会抛，交由 sync 收敛状态
            log.warning("撤单 %s 失败（可能已终态）: %s", external_id, exc)
            return False

    # ------------------------------------------------------------------ #
    def fetch_balance(self) -> AdapterBalance:
        ex = self._exchange()
        try:
            balance = ex.fetch_balance()
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"获取余额失败: {exc}") from exc
        quote = (balance.get(self.quote_currency) or {}) if isinstance(balance, dict) else {}
        cash = float(quote.get("total") or 0.0)
        equity = float((balance.get("total") or {}).get(self.quote_currency) or cash)
        return AdapterBalance(cash=cash, equity=equity, currency=self.quote_currency)

    def fetch_positions(self, symbols: Iterable[str]) -> dict[str, float]:
        ex = self._exchange()
        try:
            balance = ex.fetch_balance()
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"获取持仓失败: {exc}") from exc
        out: dict[str, float] = {}
        for symbol in symbols:
            inst = parse_instrument(symbol)
            base = inst.code.split("/")[0]
            entry = (balance.get(base) or {}) if isinstance(balance, dict) else {}
            qty = float(entry.get("total") or 0.0)
            if qty > 0:
                out[inst.id] = qty
        return out

    def sync(self) -> list[AdapterFill | AdapterStatus]:
        ex = self._exchange()
        events: list[AdapterFill | AdapterStatus] = []
        for external_id, pair in list(self._tracked.items()):
            try:
                raw = ex.fetch_order(external_id, pair)
            except Exception as exc:  # noqa: BLE001
                log.warning("查询委托 %s 失败: %s", external_id, exc)
                continue
            status = _STATUS_MAP.get(str(raw.get("status") or "").lower(), "open")
            filled = float(raw.get("filled") or 0.0)
            avg = float(raw.get("average") or raw.get("price") or 0.0)
            if avg <= 0 and filled > 0:
                cost = float(raw.get("cost") or 0.0)
                avg = cost / filled if filled else 0.0
            fee = _cumulative_fee(raw)
            if filled > 0:
                events.append(
                    AdapterFill(
                        external_id=external_id,
                        filled=filled,
                        avg_price=avg,
                        fee=fee,
                        dt=_ts(raw.get("lastTradeTimestamp") or raw.get("timestamp")),
                    )
                )
            if status != "open":
                events.append(AdapterStatus(external_id=external_id, status=status))
                self._tracked.pop(external_id, None)
        return events

    def quote(self, symbols: Iterable[str]) -> dict[str, float]:
        ex = self._exchange()
        out: dict[str, float] = {}
        for symbol in symbols:
            inst = parse_instrument(symbol)
            try:
                ticker = ex.fetch_ticker(inst.code)
                price = float(ticker.get("last") or 0.0)
            except Exception as exc:  # noqa: BLE001
                log.warning("报价 %s 失败: %s", inst.code, exc)
                continue
            if price > 0:
                out[inst.id] = price
        return out


def _cumulative_fee(raw: dict[str, Any]) -> float:
    """累计费用：优先 ``fees`` 求和，其次单笔 ``fee``。"""
    fees = raw.get("fees")
    if isinstance(fees, list) and fees:
        total = 0.0
        for item in fees:
            try:
                total += float((item or {}).get("cost") or 0.0)
            except (TypeError, ValueError):
                continue
        if total > 0:
            return total
    fee = raw.get("fee") or {}
    try:
        return float(fee.get("cost") or 0.0) if isinstance(fee, dict) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _ts(value: Any) -> datetime | None:
    try:
        if not value:
            return None
        return datetime.fromtimestamp(int(value) / 1000.0, tz=UTC).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


__all__ = ["CcxtAdapter"]
