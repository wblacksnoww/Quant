"""交易日历。

真实交易日历来自 akshare（``tool_trade_date_hist_sina``），首次拉取后落盘缓存；
断网或未安装 akshare 时自动退化为「工作日」近似，并在日志里明确标注，
避免悄悄用错日历影响回测结论。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_CACHE_FILE = Path(__file__).resolve().parents[2] / "data" / "calendar.json"
_memory: set[date] | None = None


def _load_disk() -> set[date] | None:
    if not _CACHE_FILE.exists():
        return None
    try:
        raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        return {date.fromisoformat(d) for d in raw}
    except Exception:  # noqa: BLE001
        log.warning("交易日历缓存损坏，将重新拉取: %s", _CACHE_FILE)
        return None


def _save_disk(days: set[date]) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(d.isoformat() for d in days)), encoding="utf-8")
    tmp.replace(_CACHE_FILE)


def _fetch_from_akshare() -> set[date] | None:
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        values = df["trade_date"].tolist()
        return {_as_date(v) for v in values}
    except Exception as exc:  # noqa: BLE001
        log.warning("拉取真实交易日历失败，退化为工作日近似: %s", exc)
        return None


def _as_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def trading_days(
    start: date | None = None, end: date | None = None, *, market: str = "CN"
) -> list[date]:
    """返回 ``[start, end]`` 区间内的交易日（升序）。

    真实日历（akshare）只有 A 股口径；美股/港股退化为工作日近似，
    虚拟货币为 7×24 全周日历（无休市日）；场外基金沿用工作日近似。
    反正回测/纸盘回放的时间轴最终由行情 bar 决定，日历只影响"哪天该有数据"。
    """
    key = str(market).strip().upper()
    if key == "CRYPTO":
        return calendar_range(start, end)
    if key not in ("", "CN"):
        return weekday_range(start, end)
    global _memory
    if _memory is None:
        _memory = _load_disk()
        if _memory is None:
            fetched = _fetch_from_akshare()
            if fetched:
                _memory = fetched
                _save_disk(fetched)
    days = _memory if _memory is not None else None
    if days is None:
        return weekday_range(start, end)
    lo = start or min(days)
    hi = end or max(days)
    return sorted(d for d in days if lo <= d <= hi)


def weekday_range(start: date | None = None, end: date | None = None) -> list[date]:
    """工作日近似（忽略法定节假日）。"""
    if start is None or end is None:
        raise ValueError("工作日近似需要显式 start/end")
    out: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def calendar_range(start: date | None = None, end: date | None = None) -> list[date]:
    """全周日历（虚拟货币 7×24 无休市日）。"""
    if start is None or end is None:
        raise ValueError("全周日历需要显式 start/end")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def is_trading_day(day: date) -> bool:
    return day in set(trading_days(day, day))


def next_trading_day(day: date, *, inclusive: bool = False) -> date:
    start = day if inclusive else day + timedelta(days=1)
    for candidate in trading_days(start, start + timedelta(days=30)):
        return candidate
    return start


def prev_trading_day(day: date, *, inclusive: bool = False) -> date:
    end = day if inclusive else day - timedelta(days=1)
    days = trading_days(end - timedelta(days=30), end)
    return days[-1] if days else end


def is_market_open(market: str = "CN", moment: datetime | None = None) -> bool:
    """是否处于对应市场的连续竞价时段（自动按市场换算时区）。

    * A 股：09:30-11:30、13:00-15:00（北京时间）
    * 港股：09:30-12:00、13:00-16:00（北京时间）
    * 美股：09:30-16:00（按美东时间；夏令时 UTC-4 / 冬令时 UTC-5 统一按 UTC-4 近似，
      冬令时多算 1 小时）
    * 虚拟货币：7×24 恒开（任何时刻均为开市）
    """
    from datetime import timedelta, timezone

    offsets = {"CN": 8, "HK": 8, "US": -4}
    windows = {
        "CN": ((9, 30), (11, 30), (13, 0), (15, 0)),
        "HK": ((9, 30), (12, 0), (13, 0), (16, 0)),
        "US": ((9, 30), (16, 0), None, None),
    }
    key = str(market).strip().upper() or "CN"
    if key == "CRYPTO":
        return True
    if key not in offsets:
        key = "CN"
    local = (moment or datetime.now(timezone.utc)).astimezone(
        timezone(timedelta(hours=offsets[key]))
    )
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute

    def _within(begin: tuple[int, int], end: tuple[int, int]) -> bool:
        return begin[0] * 60 + begin[1] <= minutes < end[0] * 60 + end[1]

    session = windows[key]
    return _within(session[0], session[1]) or (
        session[2] is not None and _within(session[2], session[3])  # type: ignore[arg-type]
    )


__all__ = [
    "calendar_range",
    "is_market_open",
    "is_trading_day",
    "next_trading_day",
    "prev_trading_day",
    "trading_days",
    "weekday_range",
]
