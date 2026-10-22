"""新闻/政策因子：原始条目 → 可回测的时间对齐因子。"""

from .factors import NewsFactor, compute_factor, parse_window
from .provider import NewsFactorProvider

__all__ = ["NewsFactor", "NewsFactorProvider", "compute_factor", "parse_window"]
