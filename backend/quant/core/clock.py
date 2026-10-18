"""时钟抽象。

策略/引擎里绝不能直接调 ``datetime.now()``：那样回测就跑不了。
统一从 :class:`Clock` 取时间，回测用模拟时钟，纸盘用真实时钟。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")


class Clock(ABC):
    """时间源。"""

    @property
    @abstractmethod
    def now(self) -> datetime:
        """当前时间（带时区）。"""

    @property
    def today(self) -> date:
        return self.now.date()

    @property
    def is_simulated(self) -> bool:
        return False

    def local(self) -> datetime:
        """转换到 A 股所在时区，用于判断交易时段。"""
        return self.now.astimezone(CN_TZ)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} now={self.now.isoformat()}>"


class SimClock(Clock):
    """回测用模拟时钟，由引擎在每根 bar 上推进。"""

    def __init__(self, start: datetime | date | str) -> None:
        self._now = _coerce(start)

    @property
    def now(self) -> datetime:
        return self._now

    @property
    def is_simulated(self) -> bool:
        return True

    def set(self, moment: datetime | date | str) -> None:
        self._now = _coerce(moment)

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


class RealClock(Clock):
    """纸盘/实盘用真实时钟。"""

    def __init__(self, tz: timezone | ZoneInfo = CN_TZ) -> None:
        self._tz = tz

    @property
    def now(self) -> datetime:
        return datetime.now(self._tz)

    def __init_subclass__(cls, **kwargs: object) -> None:  # pragma: no cover
        super().__init_subclass__(**kwargs)


def _coerce(value: datetime | date | str) -> datetime:
    """把宽松输入统一成带时区的 datetime。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=CN_TZ)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=CN_TZ)
    if isinstance(value, str):
        text = value.strip().replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=CN_TZ)
            except ValueError:
                continue
    raise ValueError(f"无法解析时间: {value!r}")


__all__ = ["CN_TZ", "Clock", "RealClock", "SimClock"]
