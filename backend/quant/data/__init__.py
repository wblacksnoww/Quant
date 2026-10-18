"""数据层：Provider 抽象、缓存、交易日历。

导入本包会触发内置 Provider 的注册。
"""

from .base import (
    ALL_COLUMNS,
    DataProvider,
    SymbolInfo,
    available_providers,
    bars_to_frame,
    create_provider,
    frame_to_bars,
    normalize_frame,
    register_provider,
    to_date,
)
from .calendar import (
    is_market_open,
    is_trading_day,
    next_trading_day,
    prev_trading_day,
    trading_days,
)
from .store import DataStore, MarketDataService

# 触发内置数据源注册（副作用导入）
from .providers import AkShareProvider, CsvProvider, SyntheticProvider  # noqa: F401,E402

__all__ = [
    "ALL_COLUMNS",
    "AkShareProvider",
    "CsvProvider",
    "DataProvider",
    "DataStore",
    "MarketDataService",
    "SymbolInfo",
    "SyntheticProvider",
    "available_providers",
    "bars_to_frame",
    "create_provider",
    "frame_to_bars",
    "is_market_open",
    "is_trading_day",
    "next_trading_day",
    "normalize_frame",
    "prev_trading_day",
    "register_provider",
    "to_date",
    "trading_days",
]
