"""引擎层：回测 / 纸盘，共用 TradingSession。"""

from .base import BaseEngine, EngineConfig, EngineResult
from .backtest import BacktestEngine
from .paper import PaperEngine, PaperManager, PaperOptions, PaperRunner
from .session import SessionStats, TradingSession, bar_from_frame

__all__ = [
    "BacktestEngine",
    "BaseEngine",
    "EngineConfig",
    "EngineResult",
    "PaperEngine",
    "PaperManager",
    "PaperOptions",
    "PaperRunner",
    "SessionStats",
    "TradingSession",
    "bar_from_frame",
]
