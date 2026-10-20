"""CSV 数据源：从本地目录读取历史行情，离线可用。

目录结构::

    data/cache/600519.csv
    data/cache/000001.csv

文件列名支持中英文，会自动走 :func:`~quant.data.base.normalize_frame` 归一化。
**这个源在容器里特别有用**：把历史数据挂载进去，回测完全不依赖外网。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import pandas as pd

from ...core.errors import DataNotFound
from ..base import DataProvider, normalize_frame, register_provider

log = logging.getLogger(__name__)


@register_provider("csv")
class CsvProvider(DataProvider):
    supports_realtime = False

    def __init__(self, root: str | Path = "data/cache") -> None:
        self.root = Path(root)
        if not self.root.is_absolute():
            self.root = (Path(__file__).resolve().parents[3] / self.root).resolve()

    # ------------------------------------------------------------------ #
    def _path(self, symbol: str) -> Path:
        from ...core.markets import try_parse

        inst = try_parse(symbol)
        code = inst.code if inst else str(symbol).strip()
        for suffix in (".csv", ".parquet", ".pkl"):
            candidate = self.root / f"{code}{suffix}"
            if candidate.exists():
                return candidate
        raise DataNotFound(f"{self.root} 下找不到 {code} 的行情文件")

    def _read(self, symbol: str) -> pd.DataFrame:
        path = self._path(symbol)
        if path.suffix == ".csv":
            frame = pd.read_csv(path)
        elif path.suffix == ".parquet":
            frame = pd.read_parquet(path)
        else:
            frame = pd.read_pickle(path)
        return frame

    def daily(self, symbol: str, start: date, end: date, *, adjust: str = "qfq") -> pd.DataFrame:
        frame = normalize_frame(self._read(symbol), symbol=symbol)
        mask = (frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))
        sliced = frame.loc[mask].reset_index(drop=True)
        if len(sliced) == 0:
            raise DataNotFound(f"CSV 中 {symbol} 在 {start}~{end} 无数据")
        return sliced

    # ------------------------------------------------------------------ #
    def available_symbols(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted({p.stem for p in self.root.iterdir() if p.suffix in (".csv", ".parquet")})

    def realtime(self, symbols: Iterable[str]) -> dict[str, float]:
        """用最后一根收盘价冒充实时价（离线演示用，明确标注）。"""
        from ...core.markets import try_parse

        prices: dict[str, float] = {}
        for symbol in symbols:
            try:
                frame = normalize_frame(self._read(symbol), symbol=symbol)
                inst = try_parse(symbol)
                key = inst.id if inst else str(symbol)
                prices[key] = float(frame["close"].iloc[-1])
            except Exception:  # noqa: BLE001
                continue
        return prices


__all__ = ["CsvProvider"]
