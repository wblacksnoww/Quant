"""内置策略集合。新增策略文件放到本目录即会被自动发现。"""

from .buy_hold import BuyHoldStrategy
from .dual_ma import DualMaStrategy
from .momentum import MomentumRotationStrategy
from .news_dual_ma import NewsDualMaStrategy
from .rsi_reversion import RsiReversionStrategy

__all__ = [
    "BuyHoldStrategy",
    "DualMaStrategy",
    "MomentumRotationStrategy",
    "NewsDualMaStrategy",
    "RsiReversionStrategy",
]
