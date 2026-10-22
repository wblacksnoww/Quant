"""默认新闻源配置（可用 QUANT_NEWS_FEEDS 覆盖）。"""

from __future__ import annotations

DEFAULT_FEEDS: list[dict] = [
    {
        "name": "华尔街见闻-区块链",
        "kind": "wallstreetcn",
        "url": "https://api-one.wallstcn.com/apiv1/content/lives?channel=blockchain-channel&limit=50",
        "market": "CRYPTO",
        "force": False,
    },
    {
        "name": "华尔街见闻-宏观",
        "kind": "wallstreetcn",
        "url": "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=50",
        "market": "",
        "force": False,
    },
    {
        "name": "Cointelegraph",
        "kind": "rss",
        "url": "https://cointelegraph.com/rss",
        "market": "CRYPTO",
        "force": True,
    },
    {
        "name": "CoinDesk",
        "kind": "rss",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "market": "CRYPTO",
        "force": True,
    },
]

__all__ = ["DEFAULT_FEEDS"]
