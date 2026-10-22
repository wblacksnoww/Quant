"""策略侧的新闻因子提供器：从数据库加载条目，按当前 bar 时间计算 PIT 因子。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from datetime import datetime

from ..storage.db import Database
from ..storage.repositories.news import NewsRepository
from .factors import NewsFactor, compute_factor

log = logging.getLogger(__name__)


class NewsFactorProvider:
    def __init__(
        self,
        db: Database,
        symbols: Iterable[str],
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> None:
        self.db = db
        self.symbols = list(symbols)
        self.start = start
        self.end = end
        self._items: list[dict] = []
        self._lock = threading.RLock()
        self.refresh()

    def refresh(self) -> None:
        try:
            with self.db.session() as session:
                rows = NewsRepository(session).for_symbols(
                    self.symbols,
                    start=self.start,
                    end=self.end,
                    limit=5000,
                )
                items = [row.to_dict() for row in rows]
            with self._lock:
                self._items = items
        except Exception:  # noqa: BLE001 - 新闻不可用不应阻断交易
            log.warning("加载新闻因子失败，策略按中性因子运行", exc_info=True)

    def factor(
        self,
        symbol: str,
        *,
        as_of: datetime,
        window: str = "1h",
        half_life_hours: float = 24.0,
    ) -> NewsFactor:
        with self._lock:
            items = list(self._items)
        if not items:
            return NewsFactor.neutral(symbol, as_of, window)
        return compute_factor(
            items,
            symbol=symbol,
            as_of=as_of,
            window=window,
            half_life_hours=half_life_hours,
        )


__all__ = ["NewsFactorProvider"]
