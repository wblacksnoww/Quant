"""新闻/政策因子计算（纯函数，便于测试与回测复现）。"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

_WINDOWS = {
    "5m": 5 / 60,
    "15m": 15 / 60,
    "30m": 0.5,
    "1h": 1.0,
    "4h": 4.0,
    "1d": 24.0,
    "7d": 24.0 * 7,
}


def parse_window(window: str) -> float:
    """返回时间窗小时数；未知窗口按 1 小时。"""
    return _WINDOWS.get(str(window or "").strip().lower(), 1.0)


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


@dataclass
class NewsFactor:
    symbol: str
    as_of: datetime
    window: str
    sentiment: float = 0.0
    policy_risk: float = 0.0
    confidence: float = 0.0
    event_count: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def neutral(cls, symbol: str, as_of: datetime, window: str = "1h") -> NewsFactor:
        return cls(symbol=symbol, as_of=_naive_utc(as_of), window=window)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "window": self.window,
            "sentiment": round(self.sentiment, 6),
            "policy_risk": round(self.policy_risk, 6),
            "confidence": round(self.confidence, 6),
            "event_count": self.event_count,
            "items": self.items,
        }


def _item_matches(raw: dict[str, Any], symbol: str) -> bool:
    target = symbol.upper()
    symbols = {str(item).upper() for item in (raw.get("symbols") or [])}
    if target in symbols or "ALL" in symbols:
        return True
    market = target.split(":", 1)[0] if ":" in target else ""
    return bool(raw.get("market")) and str(raw["market"]).upper() == market


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _naive_utc(value)
    if isinstance(value, str) and value:
        try:
            return _naive_utc(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


def _item_dt(raw: dict[str, Any]) -> datetime | None:
    """条目对策略"可见"的时刻（PIT 时钟）。

    取 ``published_at``（新闻公开发布的时间），而不是 ``effective_at``
    （我们的入库时间）。原因很实际：``effective_at`` 由抓取任务写入，历史
    新闻补录时会被统一盖成"补录当天"。早前用它做时钟，导致一次补录之后
    整段历史新闻在回测里**全部落在未来**，任何 bar 都看不到 —— 实测 A 股
    日线 202/202 根 bar 的新闻因子恒为中性，"带新闻的策略"退化成纯双均线，
    且没有任何报错。

    用 ``published_at`` 表示"信息在那一刻已经公开，策略本可以读到"，这才是
    回测该有的口径；两者都用 ``min`` 取更早的一个，是为了在 ``published_at``
    缺失或被填成未来时间时退回入库时刻，避免真的引入未来函数。
    """
    published = _parse_dt(raw.get("published_at"))
    effective = _parse_dt(raw.get("effective_at"))
    if published is not None and effective is not None:
        return min(published, effective)
    return published if published is not None else effective


def compute_factor(
    items: Iterable[dict[str, Any]],
    *,
    symbol: str,
    as_of: datetime,
    window: str = "1h",
    half_life_hours: float = 24.0,
) -> NewsFactor:
    """按新闻的可见时刻做 Point-in-Time 聚合。

    * ``sentiment``：按置信度与时间衰减加权平均，[-1, 1]；
    * ``policy_risk``：窗口内最大风险（取 ``policy_risk * confidence``，避免被平均值稀释）；
    * ``confidence``：窗口内最高置信度；
    * ``items``：按影响强度排序的前 5 条，供前端展示。
    """
    as_of_naive = _naive_utc(as_of)
    hours = parse_window(window)
    cutoff = as_of_naive - timedelta(hours=hours)
    half_life = max(0.1, float(half_life_hours))

    selected: list[tuple[float, dict[str, Any]]] = []
    for raw in items:
        if not _item_matches(raw, symbol):
            continue
        effective = _item_dt(raw)
        if effective is None or effective > as_of_naive or effective < cutoff:
            continue
        age_hours = max(0.0, (as_of_naive - effective).total_seconds() / 3600.0)
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
        weight = confidence * math.pow(0.5, age_hours / half_life)
        if weight <= 0:
            continue
        selected.append((weight, raw))

    if not selected:
        return NewsFactor.neutral(symbol, as_of_naive, window)

    total_weight = sum(weight for weight, _ in selected)
    sentiment = sum(weight * float(raw.get("sentiment") or 0.0) for weight, raw in selected) / total_weight
    policy_risk = max(
        max(0.0, min(1.0, float(raw.get("policy_risk") or 0.0)))
        * max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
        for _, raw in selected
    )
    confidence = max(max(0.0, min(1.0, float(raw.get("confidence") or 0.0))) for _, raw in selected)
    ranked = sorted(
        selected,
        key=lambda pair: (
            abs(float(pair[1].get("sentiment") or 0.0)) * float(pair[1].get("confidence") or 0.0)
            + float(pair[1].get("policy_risk") or 0.0)
        ),
        reverse=True,
    )
    return NewsFactor(
        symbol=symbol,
        as_of=as_of_naive,
        window=window,
        sentiment=sentiment,
        policy_risk=policy_risk,
        confidence=confidence,
        event_count=len(selected),
        items=[dict(raw) for _, raw in ranked[:5]],
    )


__all__ = ["NewsFactor", "compute_factor", "parse_window"]
