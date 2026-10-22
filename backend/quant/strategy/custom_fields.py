"""条件拼装/蓝图可用的字段白名单（价格 + 新闻/政策因子）。"""

from __future__ import annotations

PRICE_FIELDS: tuple[str, ...] = ("close", "open", "high", "low", "volume", "amount")

# field -> (NewsFactor 属性, 聚合窗口)
NEWS_FIELDS: dict[str, tuple[str, str]] = {
    "news_sentiment": ("sentiment", "1h"),
    "news_policy_risk": ("policy_risk", "1h"),
    "news_confidence": ("confidence", "1h"),
    "news_event_count": ("event_count", "1h"),
    "news_sentiment_1d": ("sentiment", "1d"),
    "news_policy_risk_1d": ("policy_risk", "1d"),
}

ALL_FIELDS: tuple[str, ...] = PRICE_FIELDS + tuple(NEWS_FIELDS)

__all__ = ["ALL_FIELDS", "NEWS_FIELDS", "PRICE_FIELDS"]
