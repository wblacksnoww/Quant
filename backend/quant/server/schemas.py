"""API 请求/响应模型。

Pydantic v2 负责三件事：入参校验、OpenAPI 文档、前端类型生成的依据。
所有金额/比例字段都带上下界，避免"策略传了个负数仓位"这类问题穿透到引擎。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #


class Message(BaseModel):
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


# --------------------------------------------------------------------------- #
# 认证
# --------------------------------------------------------------------------- #


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    totp_code: str | None = Field(default=None, max_length=10)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int
    user: "UserOut"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    totp_enabled: bool
    last_login_at: str | None = None
    created_at: str | None = None


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class TotpSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str
    recovery_codes: list[str]
    issuer: str


class TotpEnableRequest(BaseModel):
    code: str = Field(min_length=6, max_length=10)


class TotpDisableRequest(BaseModel):
    password: str
    code: str


class SessionOut(BaseModel):
    id: int
    jti: str
    ip: str
    user_agent: str
    created_at: str
    expires_at: str


# --------------------------------------------------------------------------- #
# 行情
# --------------------------------------------------------------------------- #


class BarOut(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    prev_close: float | None = None
    limit_up: float | None = None
    limit_down: float | None = None


class BarsResponse(BaseModel):
    symbol: str
    code: str = ""
    market: str = "CN"
    currency: str = "CNY"
    name: str = ""
    bars: list[BarOut]
    source: str = ""
    cached: bool = False


class QuoteOut(BaseModel):
    symbol: str
    name: str = ""
    price: float = 0.0
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None  # 股（三市场统一）
    amount: float | None = None  # 本币元
    change: float | None = None
    change_pct: float | None = None
    ts: str = ""
    source: str = ""
    stale: bool = False
    market: str = "CN"
    currency: str = "CNY"


class QuoteResponse(BaseModel):
    quotes: dict[str, QuoteOut]
    ts: str
    note: str = ""


class SymbolOut(BaseModel):
    symbol: str
    code: str = ""
    name: str = ""
    board: str = ""
    industry: str = ""
    is_st: bool = False
    market: str = "CN"
    kind: str = "stock"  # stock / etf / index / other


class MarketOut(BaseModel):
    """市场元信息，前端市场分类标签用。"""

    code: str  # CN / US / HK
    label: str  # A股 / 美股 / 港股
    currency: str  # CNY / USD / HKD
    currency_symbol: str  # ¥ / $ / HK$


# --------------------------------------------------------------------------- #
# 策略
# --------------------------------------------------------------------------- #


class ParamOut(BaseModel):
    name: str
    type: str
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: list[Any] = Field(default_factory=list)
    description: str = ""


class StrategyOut(BaseModel):
    id: str
    name: str
    description: str
    category: str
    params: list[ParamOut]


# --------------------------------------------------------------------------- #
# 用户自建策略（条件拼装）
# --------------------------------------------------------------------------- #


class CustomStrategyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=255)
    spec: dict[str, Any]


class CustomStrategyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=255)
    spec: dict[str, Any] | None = None
    enabled: bool | None = None


class CustomStrategyOut(BaseModel):
    id: int
    user_id: int
    name: str
    description: str
    category: str
    spec: dict[str, Any]
    enabled: bool
    created_at: str | None = None
    updated_at: str | None = None


# --------------------------------------------------------------------------- #
# 回测
# --------------------------------------------------------------------------- #


class CostInput(BaseModel):
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

    @field_validator("symbols")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        from ..core.markets import Instrument, Market, parse_instrument

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


# --------------------------------------------------------------------------- #
# 纸盘
# --------------------------------------------------------------------------- #


class PaperStartRequest(BacktestRequest):
    mode: Literal["replay", "live"] = "replay"
    poll_interval: float = Field(default=5.0, ge=0.5, le=600)
    replay_days: int = Field(default=120, ge=10, le=1000)
    replay_interval: float = Field(default=0.35, ge=0.0, le=10)
    respect_market_hours: bool = True


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


# --------------------------------------------------------------------------- #
# 风控
# --------------------------------------------------------------------------- #


class RiskRuleOut(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    severity: str
    params: dict[str, Any] = Field(default_factory=dict)
    trigger_count: int = 0


# --------------------------------------------------------------------------- #
# 凭据
# --------------------------------------------------------------------------- #


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["broker", "datasource"]
    payload: dict[str, Any] = Field(min_length=1)
    description: str = Field(default="", max_length=255)


class CredentialOut(BaseModel):
    id: int
    name: str
    kind: str
    description: str
    created_at: str | None = None
    updated_at: str | None = None


# --------------------------------------------------------------------------- #
# 系统
# --------------------------------------------------------------------------- #


class HealthOut(BaseModel):
    status: str
    version: str
    env: str
    database: bool
    uptime_seconds: float


class SystemInfo(BaseModel):
    name: str
    version: str
    env: str
    python: str
    database: str
    data_provider: str
    available_providers: list[str]
    encryption: dict[str, Any]
    auth: dict[str, Any]
    paper_running: bool
    strategies: list[str]
    warnings: list[str] = Field(default_factory=list)


class AuditOut(BaseModel):
    id: int
    ts: str
    actor: str
    action: str
    target: str
    detail: dict[str, Any] = Field(default_factory=dict)
    ip: str
    prev_hash: str
    hash: str


class AuditVerifyOut(BaseModel):
    valid: bool
    checked: int
    message: str


class DataCacheOut(BaseModel):
    symbol: str
    code: str = ""
    market: str = "CN"
    rows: int
    start: str | None
    end: str | None
    size_kb: float


TokenResponse.model_rebuild()

__all__ = [
    "AuditOut",
    "AuditVerifyOut",
    "BacktestRequest",
    "BarOut",
    "BarsResponse",
    "ChangePasswordRequest",
    "CostInput",
    "CredentialCreate",
    "CredentialOut",
    "DataCacheOut",
    "ErrorBody",
    "HealthOut",
    "LoginRequest",
    "MarketOut",
    "Message",
    "PaperStartRequest",
    "PaperStateOut",
    "ParamOut",
    "QuoteOut",
    "QuoteResponse",
    "RefreshRequest",
    "RiskRuleInput",
    "RiskRuleOut",
    "RunAccepted",
    "RunDetail",
    "RunSummary",
    "SessionOut",
    "StrategyOut",
    "SymbolOut",
    "SystemInfo",
    "TokenResponse",
    "TotpDisableRequest",
    "TotpEnableRequest",
    "TotpSetupResponse",
    "UserOut",
]
