"""交易成本模型（A 股 / 美股 / 港股）。

A 股规则依据（2024 年起沪深两市统一口径）：

* **佣金**：双向收取 ``佣金率 × 成交金额``，不足最低值按最低值收（默认 5 元）。
* **印花税**：**仅卖出**收取，税率 0.05%（2023-08-28 由 0.1% 下调）。
* **过户费**：双向收取，费率 0.001%（万分之 0.1）。
* **滑点**：按基点折算成价格偏移，买上卖下，并把偏移额计入费用明细，
  这样"成交价"始终是策略看到的价格，成本不会藏在价格里让人误解。

境外市场的默认费率是**近似值**（各家券商差异大，正式使用请在请求里覆盖）：

* **美股**（``CostModel.for_market("US")``）：佣金 0.01%、无最低佣金、
  无印花税与过户费；无涨跌停、T+0 见 :mod:`quant.execution.rules`。
* **港股**（``CostModel.for_market("HK")``）：佣金 0.01%、印花税 0.1% **双边**、
  无最低佣金（真实还有交易征费/交易费/结算费等小额费项，这里忽略）。
* **虚拟货币**（``CostModel.for_market("CRYPTO")``）：佣金 0.1% **双边**、无最低佣金、
  无印花税与过户费（各家交易所差异大，正式使用请在请求里覆盖）。
* **场外基金**（``CostModel.for_market("FUND")``）：引擎费用全 0 —— 申赎费在
  基金模块按申赎记录单独记账，不经过本模型。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.types import FeeBreakdown, Side


@dataclass(frozen=True, slots=True)
class CostModel:
    """默认按 A 股口径；境外市场用 :meth:`for_market` 取预设。"""

    commission_rate: float = 3e-4  # 佣金 0.03%
    min_commission: float = 5.0  # 单笔最低佣金（元）
    stamp_tax_rate: float = 5e-4  # 印花税 0.05%，仅卖出（stamp_both_sides=True 时双边）
    transfer_fee_rate: float = 1e-5  # 过户费 0.001%，双向
    slippage_bps: float = 0.0  # 滑点（基点，1bp = 0.01%）
    stamp_both_sides: bool = False  # 港股印花税买卖双边

    # ------------------------------------------------------------------ #
    def _slippage_rate(self) -> float:
        return self.slippage_bps / 10_000.0

    def slippage_price(self, side: Side, price: float) -> float:
        """把滑点折进价格：买入抬价、卖出压价。"""
        rate = self._slippage_rate()
        if rate == 0:
            return price
        return price * (1.0 + rate) if side is Side.BUY else price * (1.0 - rate)

    def fees(
        self, side: Side, price: float, quantity: float, *, gross: float | None = None
    ) -> FeeBreakdown:
        """计算一笔成交的全部费用。

        ``gross`` 允许显式传入成交金额（当成交价与参考价不同时可保持口径一致）。
        滑点成本按"参考价 × 数量"的偏移计入。
        """
        value = float(gross) if gross is not None else abs(float(price) * float(quantity))
        commission = max(self.min_commission, self.commission_rate * value) if value > 0 else 0.0
        stamp = 0.0
        if value > 0 and (side is Side.SELL or self.stamp_both_sides):
            stamp = self.stamp_tax_rate * value
        transfer_fee = self.transfer_fee_rate * value if value > 0 else 0.0
        return FeeBreakdown(
            commission=commission,
            stamp_tax=stamp,
            transfer_fee=transfer_fee,
            slippage=0.0,
        )

    # ------------------------------------------------------------------ #
    def estimate_buy_cost(self, price: float, quantity: float) -> float:
        """买入所需现金总额（成交额 + 费用），用于资金校验与下单量折算。"""
        value = abs(price * quantity)
        fees = self.fees(Side.BUY, price, quantity, gross=value)
        return value + fees.total

    def estimate_sell_proceeds(self, price: float, quantity: float) -> float:
        """卖出可回收现金（成交额 - 费用）。"""
        value = abs(price * quantity)
        fees = self.fees(Side.SELL, price, quantity, gross=value)
        return value - fees.total

    def max_affordable_quantity(self, cash: float, price: float, lot_size: int = 100) -> float:
        """在给定现金和价格下可买入的最大数量。

        整手市场（``lot_size > 1``）返回整手的整数倍；任意数量市场
        （虚拟货币等）按费用二分逼近返回小数数量。
        """
        if price <= 0 or cash <= 0:
            return 0.0
        if lot_size > 1:
            raw = int(cash / price)
            qty = (raw // lot_size) * lot_size
            while qty > 0 and self.estimate_buy_cost(price, qty) > cash:
                qty -= lot_size
            return float(max(0, qty))
        lo, hi = 0.0, cash / price
        for _ in range(64):
            mid = (lo + hi) / 2.0
            if self.estimate_buy_cost(price, mid) <= cash:
                lo = mid
            else:
                hi = mid
        return lo

    def to_dict(self) -> dict[str, float | bool]:
        return {
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "stamp_tax_rate": self.stamp_tax_rate,
            "transfer_fee_rate": self.transfer_fee_rate,
            "slippage_bps": self.slippage_bps,
            "stamp_both_sides": self.stamp_both_sides,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> CostModel:
        if not data:
            return cls()
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}  # type: ignore[attr-defined]
        return cls(**known)

    @classmethod
    def for_market(cls, market: str, overrides: dict | None = None) -> CostModel:
        """按市场取默认费率；``overrides`` 非空时以显式参数为准。

        注意：``overrides`` 来自 API 的 ``cost`` 输入（意味着用户显式给出了
        一组完整费率），此时不再叠加市场预设，避免"改了一个字段、
        其余悄悄变成另一个市场的默认值"这类难以察觉的行为。
        """
        if overrides:
            return cls.from_dict(overrides)
        presets = {
            "CN": cls(),
            "US": cls(
                commission_rate=1e-4,
                min_commission=0.0,
                stamp_tax_rate=0.0,
                transfer_fee_rate=0.0,
            ),
            "HK": cls(
                commission_rate=1e-4,
                min_commission=0.0,
                stamp_tax_rate=1e-3,
                transfer_fee_rate=0.0,
                stamp_both_sides=True,
            ),
            "CRYPTO": cls(
                commission_rate=1e-3,
                min_commission=0.0,
                stamp_tax_rate=0.0,
                transfer_fee_rate=0.0,
            ),
            # 场外基金不经引擎撮合；这里给全 0 预设避免静默回退到 A 股费率
            "FUND": cls(
                commission_rate=0.0,
                min_commission=0.0,
                stamp_tax_rate=0.0,
                transfer_fee_rate=0.0,
            ),
        }
        return presets.get(str(market).strip().upper(), presets["CN"])


__all__ = ["CostModel"]
