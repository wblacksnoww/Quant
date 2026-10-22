"""仓储层：按聚合拆分的 ORM 访问入口。

``RunRecorder`` 位于 :mod:`quant.storage.recorder`，这里保留再导出，兼容既有 import。
"""

from ..recorder import RunRecorder
from .audit import AuditRepository
from .bootstrap import bootstrap
from .credentials import CredentialRepository
from .feedback import FeedbackRepository
from .funds import FundTradeRepository
from .news import NewsRepository
from .runs import RunRepository
from .strategies import UserStrategyRepository
from .strategy_shares import StrategyShareRepository
from .tokens import TokenRepository
from .users import UserRepository
from .watchlist import WatchlistRepository

__all__ = [
    "AuditRepository",
    "CredentialRepository",
    "FeedbackRepository",
    "FundTradeRepository",
    "NewsRepository",
    "RunRecorder",
    "RunRepository",
    "TokenRepository",
    "UserRepository",
    "WatchlistRepository",
    "StrategyShareRepository",
    "UserStrategyRepository",
    "bootstrap",
]
