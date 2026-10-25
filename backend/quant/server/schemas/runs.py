"""运行类：回测 / 纸盘 / 实盘共用的请求与状态模型。

三种模式共用 ``BacktestRequest`` 作为基类，差别只在启动参数与状态字段。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "BacktestRequest",
    "CostInput",
    "LiveAdapterOut",
    "LiveStartRequest",
    "LiveStateOut",
    "PaperStartRequest",
    "PaperStateOut",
    "RiskRuleInput",
    "RunAccepted",
    "RunDetail",
    "RunSummary",
    "SwitchStrategyRequest",
]

BAR_INTERVAL = Literal["1d", "1m", "5m", "15m", "30m", "60m"]


def dedupe_symbols(values: list[str]) -> list[str]:
    """规范化标的列表：去空、去重、校验可解析、禁止混市场。

    回测与参数寻优共用这一份实现 —— 两边如果各写一份，迟早出现
    "回测拒绝了混市场标的、寻优却放过去"这种只在特定入口才炸的差异。
    """
    from ...core.markets import Instrument, Market, parse_instrument

    seen: list[Instrument] = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        try:
            inst = parse_instrument(text)
        except ValueError as exc:
            raise ValueError(f"无法识别的标的 {item!r}：{exc}") from exc
        if inst not in seen:
            seen.append(inst)
    if not seen:
        raise ValueError("至少需要一个标的")
    markets = {inst.market for inst in seen}
    if len(markets) > 1:
        labels = "、".join(Market(m).label for m in markets)
        raise ValueError(f"同一运行只能包含单一市场的标的（当前混用了 {labels}）")
    return [inst.id for inst in seen]


class CostInput(BaseModel):
    """交易成本。全部带上界，防止"把滑点写成 10000 bps"把回测跑成负收益。"""

    commission_rate: float = Field(default=3e-4, ge=0, le=0.01)
    min_commission: float = Field(default=5.0, ge=0, le=100)
    stamp_tax_rate: float = Field(default=5e-4, ge=0, le=0.01)
    transfer_fee_rate: float = Field(default=1e-5, ge=0, le=0.01)
    slippage_bps: float = Field(default=0.0, ge=0, le=200)
    stamp_both_sides: bool = False


class RiskRuleInput(BaseModel):
    id: str
    enabled: bool = True
    limit: float | None = None
    max_orders: int | None = None
    window_seconds: float | None = None
    symbols: list[str] | None = None


class BacktestRequest(BaseModel):
    strategy_id: str
    symbols: list[str] = Field(min_length=1, max_length=30)
    start: date | None = None
    end: date | None = None
    initial_cash: float = Field(default=1_000_000, gt=0, le=1e10)
    params: dict[str, Any] = Field(default_factory=dict)
    cost: CostInput | None = None  # 不传则按市场取默认费率
    risk: list[RiskRuleInput] | None = None
    benchmark: str | None = None
    risk_free_rate: float = Field(default=0.02, ge=-0.1, le=0.5)
    # K 线周期：1d（默认）或 1m/5m/15m/30m/60m（分钟级，虚拟货币专用）
    bar_interval: BAR_INTERVAL = "1d"

    @field_validator("symbols")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        return dedupe_symbols(values)


class RunSummary(BaseModel):
    run_id: str
    kind: str
    strategy_id: str
    symbols: list[str] = Field(default_factory=list)
    status: str
    progress: float = 0.0
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str | None = None
    finished_at: str | None = None
    elapsed_seconds: float = 0.0


class RunDetail(RunSummary):
    config: dict[str, Any] = Field(default_factory=dict)
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
    trades: list[dict[str, Any]] = Field(default_factory=list)
    orders: list[dict[str, Any]] = Field(default_factory=list)
    positions: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[dict[str, Any]] = Field(default_factory=list)


class RunAccepted(BaseModel):
    run_id: str
    status: str = "accepted"
    mode: str = ""  # 实盘用来回显 "sandbox" / "live"


class PaperStartRequest(BacktestRequest):
    mode: Literal["replay", "live"] = "replay"
    poll_interval: float = Field(default=5.0, ge=0.5, le=600)
    replay_days: int = Field(default=120, ge=10, le=1000)
    replay_interval: float = Field(default=0.35, ge=0.0, le=10)
    respect_market_hours: bool = True
    bar_interval: BAR_INTERVAL = "1d"


class PaperStateOut(BaseModel):
    run_id: str
    strategy_id: str
    symbols: list[str]
    mode: str
    status: str
    started_at: str | None = None
    last_prices: dict[str, float] = Field(default_factory=dict)
    cash: float = 0.0
    frozen_cash: float = 0.0
    equity: float = 0.0
    total_return: float = 0.0
    positions: list[dict[str, Any]] = Field(default_factory=list)
    open_orders: list[dict[str, Any]] = Field(default_factory=list)
    recent_fills: list[dict[str, Any]] = Field(default_factory=list)
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    steps: int = 0


class LiveStartRequest(BacktestRequest):
    """实盘启动参数。

    ``adapter`` 目前支持 ``ccxt``（虚拟货币）与 ``miniqmt``（A 股）。
    ``credential_id`` 指向「设置 → 凭据」里 ``kind=broker`` 的记录。
    """

    adapter: Literal["ccxt", "miniqmt"] = "ccxt"
    credential_id: int
    sandbox: bool = True  # 默认测试网/模拟账户
    confirm_live: bool = False  # 关闭 sandbox 时必须显式确认
    poll_interval: float | None = Field(default=None, ge=1.0, le=600)  # 缺省取服务端配置
    warmup_days: int = Field(default=120, ge=20, le=1000)


class LiveStateOut(PaperStateOut):
    adapter: str = ""
    sandbox: bool = True
    credential_id: int | None = None
    reconcile_at: str | None = None


class SwitchStrategyRequest(BaseModel):
    """运行中切换策略（纸盘/实盘共用）。下一轮 step 生效。"""

    strategy_id: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    cancel_open_orders: bool = Field(default=True, description="切换时撤销旧策略的未成交挂单（推荐）")


class LiveAdapterOut(BaseModel):
    name: str
    supports_sandbox: bool
    requires_api_key: bool = True
    markets: list[str] = Field(default_factory=list)
