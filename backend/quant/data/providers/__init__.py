"""内置数据源。导入本包即完成注册（``@register_provider`` 的副作用）。"""

from .akshare_provider import AkShareProvider
from .csv_provider import CsvProvider
from .synthetic import SyntheticProvider, VolatileSyntheticProvider

__all__ = [
    "AkShareProvider",
    "CsvProvider",
    "SyntheticProvider",
    "VolatileSyntheticProvider",
]
