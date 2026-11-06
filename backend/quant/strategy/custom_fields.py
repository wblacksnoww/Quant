"""条件拼装/蓝图可用的字段白名单（价格 + 新闻/政策因子）。"""

from __future__ import annotations

PRICE_FIELDS: tuple[str, ...] = ("close", "open", "high", "low", "volume", "amount")

# 新闻字段 = 指标 × 聚合窗口，**按笛卡尔积生成**，不手写。
#
# 手写会漏：历史上这里只有 1h 的全部四个指标 + 1d 的情绪与政策风险，
# 而 `news_dual_ma.build_spec()` 会按用户选的 news_window 一次性拼出
# 全部四个字段名（情绪/置信度/政策风险/事件数）。于是 news_window=1d
# 一跑就在回测里 KeyError，用户看到的是"回测失败"而不是"这个参数不支持"。
#
# 字段名约定：1h 无后缀（历史兼容），其余窗口加 `_<窗口>` 后缀。
NEWS_METRICS: tuple[tuple[str, str], ...] = (
    ("sentiment", "sentiment"),
    ("policy_risk", "policy_risk"),
    ("confidence", "confidence"),
    ("event_count", "event_count"),
)
NEWS_WINDOWS: tuple[str, ...] = ("15m", "1h", "4h", "1d")


def news_field(metric: str, window: str) -> str:
    """字段名：1h 不加后缀，其余窗口加 `_<窗口>`。"""
    suffix = "" if window == "1h" else f"_{window}"
    return f"news_{metric}{suffix}"


# field -> (NewsFactor 属性, 聚合窗口)
NEWS_FIELDS: dict[str, tuple[str, str]] = {
    news_field(metric, window): (metric, window) for window in NEWS_WINDOWS for metric, _ in NEWS_METRICS
}

ALL_FIELDS: tuple[str, ...] = PRICE_FIELDS + tuple(NEWS_FIELDS)

__all__ = [
    "ALL_FIELDS",
    "NEWS_FIELDS",
    "NEWS_METRICS",
    "NEWS_WINDOWS",
    "PRICE_FIELDS",
    "news_field",
]
