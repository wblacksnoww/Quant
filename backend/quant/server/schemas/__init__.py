"""API 请求/响应模型。

Pydantic v2 负责三件事：入参校验、OpenAPI 文档、前端类型生成的依据。
所有金额/比例字段都带上下界，避免"策略传了个负数仓位"这类问题穿透到引擎。

按领域拆成多个模块，本文件只做统一出口 —— 所有外部代码继续
``from quant.server.schemas import X`` 即可，无需关心 X 落在哪个子模块。
"""

from __future__ import annotations

from .auth import (
    CaptchaOut,
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    SessionOut,
    TokenResponse,
    TotpDisableRequest,
    TotpEnableRequest,
    TotpSetupResponse,
    UserApprovalRequest,
    UserOut,
)
from .common import ErrorBody, Message
from .credentials import CredentialCreate, CredentialOut
from .feedback import FeedbackIn, FeedbackOut, FeedbackStatusUpdate
from .funds import (
    FundHoldingOut,
    FundNavOut,
    FundNavPoint,
    FundPortfolioOut,
    FundTradeCreate,
    FundTradeOut,
)
from .gateway import GatewayStartRequest, GatewayStatusOut
from .market import (
    BarOut,
    BarsResponse,
    IndexQuoteOut,
    InstrumentOut,
    MarketOut,
    QuoteOut,
    QuoteResponse,
    RankingOut,
    SymbolOut,
    WatchlistUpdate,
)
from .news import NewsFactorOut, NewsFetchStatusOut, NewsItemIn, NewsItemOut
from .risk import RiskRuleOut
from .runs import (
    BacktestRequest,
    CostInput,
    LiveAdapterOut,
    LiveStartRequest,
    LiveStateOut,
    PaperStartRequest,
    PaperStateOut,
    RiskRuleInput,
    RunAccepted,
    RunDetail,
    RunSummary,
    SwitchStrategyRequest,
)
from .strategies import (
    AdminStrategyOut,
    AdminStrategyPatch,
    CustomStrategyIn,
    CustomStrategyOut,
    CustomStrategyPatch,
    ParamOut,
    SharedStrategyOut,
    StrategyOut,
    StrategyShareIn,
    StrategyShareOut,
)
from .system import AuditOut, AuditVerifyOut, DataCacheOut, HealthOut, SystemInfo

__all__ = [
    "AdminStrategyOut",
    "AdminStrategyPatch",
    "AuditOut",
    "AuditVerifyOut",
    "BacktestRequest",
    "BarOut",
    "BarsResponse",
    "CaptchaOut",
    "ChangePasswordRequest",
    "CostInput",
    "CredentialCreate",
    "CredentialOut",
    "CustomStrategyIn",
    "CustomStrategyOut",
    "CustomStrategyPatch",
    "DataCacheOut",
    "ErrorBody",
    "FeedbackIn",
    "FeedbackOut",
    "FeedbackStatusUpdate",
    "FundHoldingOut",
    "FundNavOut",
    "FundNavPoint",
    "FundPortfolioOut",
    "FundTradeCreate",
    "FundTradeOut",
    "GatewayStartRequest",
    "GatewayStatusOut",
    "HealthOut",
    "IndexQuoteOut",
    "InstrumentOut",
    "LiveAdapterOut",
    "LiveStartRequest",
    "LiveStateOut",
    "LoginRequest",
    "MarketOut",
    "Message",
    "NewsFactorOut",
    "NewsFetchStatusOut",
    "NewsItemIn",
    "NewsItemOut",
    "PaperStartRequest",
    "PaperStateOut",
    "ParamOut",
    "QuoteOut",
    "QuoteResponse",
    "RankingOut",
    "RefreshRequest",
    "RegisterRequest",
    "RiskRuleInput",
    "RiskRuleOut",
    "RunAccepted",
    "RunDetail",
    "RunSummary",
    "SessionOut",
    "SharedStrategyOut",
    "StrategyOut",
    "StrategyShareIn",
    "StrategyShareOut",
    "SwitchStrategyRequest",
    "SymbolOut",
    "SystemInfo",
    "TokenResponse",
    "TotpDisableRequest",
    "TotpEnableRequest",
    "TotpSetupResponse",
    "UserApprovalRequest",
    "UserOut",
    "WatchlistUpdate",
]
