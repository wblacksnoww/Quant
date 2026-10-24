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
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    approval_status: str = "approved"
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


class IndexQuoteOut(BaseModel):
    """指数卡片：点数 + 涨跌（不可交易，独立于 MARKET:CODE 体系）。"""

    id: str
    name: str
    market: str
    currency: str
    price: float
    prev_close: float | None = None
    change: float | None = None
    change_pct: float | None = None


class RankingOut(BaseModel):
    """市场排行榜：涨幅榜 / 跌幅榜 / 成交额榜（热门推荐复用成交额榜）。"""

    market: str
    sort: str = "change_pct"
    order: str = "desc"
    period: str = "day"
    items: list[QuoteOut] = Field(default_factory=list)
    note: str = ""
    ts: str = ""


class SymbolOut(BaseModel):
    symbol: str
    code: str = ""
    name: str = ""
    board: str = ""
    industry: str = ""
    is_st: bool = False
    market: str = "CN"
    kind: str = "stock"  # stock / etf / index / other


class InstrumentOut(BaseModel):
    """标的详情：标识 + 交易规则 + 最新快照（详情页一屏所需）。"""

    symbol: str
    code: str
    market: str
    currency: str
    currency_symbol: str = ""
    name: str = ""
    board: str = ""
    limit_pct: float = 0.0
    lot_size: int = 1
    t_plus_1: bool = True
    price_digits: int = 2
    price: float = 0.0
    change: float | None = None
    change_pct: float | None = None
    prev_close: float | None = None
    volume: float | None = None
    amount: float | None = None
    limit_up: float | None = None
    limit_down: float | None = None
    source: str = ""


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
    version: int = 1
    source_strategy_id: int | None = None
    source_version: int | None = None
    forked_at: str | None = None
    update_available: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class SharedStrategyOut(BaseModel):
    share_id: int
    share_type: str
    strategy_id: int
    name: str
    description: str
    category: str
    spec: dict[str, Any] | None = None
    version: int
    owner_username: str
    updated_at: str | None = None
    fork_id: int | None = None
    fork_version: int | None = None
    update_available: bool = False


class StrategyShareIn(BaseModel):
    scope: Literal["public", "user"] = "public"
    target_username: str | None = Field(default=None, max_length=64)


class StrategyShareOut(BaseModel):
    id: int
    scope: str
    target_username: str | None = None
    created_at: str | None = None


class AdminStrategyOut(CustomStrategyOut):
    owner_username: str = ""
    share_count: int = 0
    fork_count: int = 0


class AdminStrategyPatch(BaseModel):
    enabled: bool


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
    # K 线周期：1d（默认）或 1m/5m/15m/30m/60m（分钟级，虚拟货币专用）
    bar_interval: Literal["1d", "1m", "5m", "15m", "30m", "60m"] = "1d"

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
    mode: str = ""  # 实盘用来回显 "sandbox" / "live"


# --------------------------------------------------------------------------- #
# 纸盘
# --------------------------------------------------------------------------- #


class PaperStartRequest(BacktestRequest):
    mode: Literal["replay", "live"] = "replay"
    poll_interval: float = Field(default=5.0, ge=0.5, le=600)
    replay_days: int = Field(default=120, ge=10, le=1000)
    replay_interval: float = Field(default=0.35, ge=0.0, le=10)
    respect_market_hours: bool = True
    bar_interval: Literal["1d", "1m", "5m", "15m", "30m", "60m"] = "1d"


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
# 实盘
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# 场外基金
# --------------------------------------------------------------------------- #


class FundNavPoint(BaseModel):
    date: str
    nav: float


class FundNavOut(BaseModel):
    symbol: str
    name: str = ""
    points: list[FundNavPoint] = Field(default_factory=list)


class FundTradeCreate(BaseModel):
    """申购填 ``amount``（支付金额），赎回填 ``shares``（申报份额）。"""

    symbol: str = Field(min_length=1, max_length=32)
    side: Literal["subscribe", "redeem"]
    trade_date: str | None = None  # YYYY-MM-DD，缺省今天
    confirm_date: str | None = None
    amount: float | None = Field(default=None, gt=0)
    shares: float | None = Field(default=None, gt=0)
    nav: float | None = Field(default=None, gt=0, description="缺省取该日或之前最近净值")
    fee_rate: float = Field(default=0.0, ge=0.0, le=0.5, description="申/赎费率，如 0.0015")
    note: str = Field(default="", max_length=255)


class FundTradeOut(BaseModel):
    id: int
    symbol: str
    name: str = ""
    side: str
    trade_date: str | None = None
    confirm_date: str | None = None
    nav: float
    shares: float
    amount: float
    fee_rate: float = 0.0
    fee: float = 0.0
    note: str = ""
    created_at: str | None = None


class FundHoldingOut(BaseModel):
    symbol: str
    name: str = ""
    shares: float
    avg_cost: float
    cost: float
    nav: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float
    total_pnl: float
    return_pct: float
    trades: int


class FundPortfolioOut(BaseModel):
    holdings: list[FundHoldingOut] = Field(default_factory=list)
    totals: dict[str, Any] = Field(default_factory=dict)
    nav_note: str = ""


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
    "FundHoldingOut",
    "FundNavOut",
    "FundNavPoint",
    "FundPortfolioOut",
    "FundTradeCreate",
    "FundTradeOut",
    "HealthOut",
    "IndexQuoteOut",
    "InstrumentOut",
    "LiveAdapterOut",
    "LiveStartRequest",
    "LiveStateOut",
    "LoginRequest",
    "MarketOut",
    "Message",
    "PaperStartRequest",
    "PaperStateOut",
    "ParamOut",
    "QuoteOut",
    "QuoteResponse",
    "RankingOut",
    "RefreshRequest",
    "RiskRuleInput",
    "RiskRuleOut",
    "RunAccepted",
    "RunDetail",
    "RunSummary",
    "SessionOut",
    "StrategyOut",
    "SwitchStrategyRequest",
    "SymbolOut",
    "SystemInfo",
    "TokenResponse",
    "TotpDisableRequest",
    "TotpEnableRequest",
    "TotpSetupResponse",
    "UserOut",
]


# --------------------------------------------------------------------------- #
# 新闻 / 政策
# --------------------------------------------------------------------------- #


class NewsItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str = ""
    url: str = ""
    symbols: list[str] = Field(default_factory=list)
    market: str = ""
    event_type: str = "other"
    sentiment: float = Field(default=0.0, ge=-1.0, le=1.0)
    policy_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    published_at: datetime | None = None
    effective_at: datetime | None = None


class NewsItemOut(BaseModel):
    id: int
    source: str = "manual"
    external_id: str | None = None
    title: str
    summary: str = ""
    url: str = ""
    published_at: str | None = None
    ingested_at: str | None = None
    effective_at: str | None = None
    symbols: list[str] = Field(default_factory=list)
    market: str = ""
    event_type: str = "other"
    sentiment: float = 0.0
    policy_risk: float = 0.0
    confidence: float = 0.0
    created_by: int | None = None
    created_at: str | None = None


class NewsFactorOut(BaseModel):
    symbol: str
    as_of: str
    window: str
    sentiment: float = 0.0
    policy_risk: float = 0.0
    confidence: float = 0.0
    event_count: int = 0
    items: list[NewsItemOut] = Field(default_factory=list)


class NewsFetchStatusOut(BaseModel):
    enabled: bool = False
    interval_seconds: float = 120.0
    feeds: list[str] = Field(default_factory=list)
    last_run_at: str | None = None
    last_result: dict[str, Any] | None = None


class WatchlistUpdate(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=30)


class CaptchaOut(BaseModel):
    token: str
    image: str
    expires_in: int = 300


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=10, max_length=128)
    captcha_token: str = Field(min_length=10, max_length=4096)
    captcha_answer: str = Field(min_length=1, max_length=8)


class FeedbackIn(BaseModel):
    category: Literal["suggestion", "bug", "other"] = "suggestion"
    content: str = Field(min_length=1, max_length=2000)


class FeedbackOut(BaseModel):
    id: int
    user_id: int | None = None
    username: str = ""
    category: str = "suggestion"
    content: str
    status: str = "new"
    created_at: str | None = None
    updated_at: str | None = None


class FeedbackStatusUpdate(BaseModel):
    status: Literal["new", "read", "resolved"]


class UserApprovalRequest(BaseModel):
    action: Literal["approve", "reject"]
