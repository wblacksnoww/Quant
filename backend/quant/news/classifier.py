"""规则型新闻分类/打分：无需外部模型，回测可复现。

新闻源只负责提供标题/正文；这里统一输出情绪、政策风险、事件类型、标的影响。
后续接 LLM 时可以替换本模块，保持数据结构不变。
"""

from __future__ import annotations

from collections.abc import Iterable

# 币种关键词 → 规范标的
CRYPTO_SYMBOLS: dict[str, str] = {
    "比特币": "CRYPTO:BTC/USDT",
    "btc": "CRYPTO:BTC/USDT",
    "以太坊": "CRYPTO:ETH/USDT",
    "ethereum": "CRYPTO:ETH/USDT",
    "eth": "CRYPTO:ETH/USDT",
    "solana": "CRYPTO:SOL/USDT",
    "sol": "CRYPTO:SOL/USDT",
    "bnb": "CRYPTO:BNB/USDT",
    "币安币": "CRYPTO:BNB/USDT",
    "瑞波": "CRYPTO:XRP/USDT",
    "xrp": "CRYPTO:XRP/USDT",
    "狗狗币": "CRYPTO:DOGE/USDT",
    "doge": "CRYPTO:DOGE/USDT",
    "艾达币": "CRYPTO:ADA/USDT",
    "ada": "CRYPTO:ADA/USDT",
    "莱特币": "CRYPTO:LTC/USDT",
    "ltc": "CRYPTO:LTC/USDT",
    "波场": "CRYPTO:TRX/USDT",
    "trx": "CRYPTO:TRX/USDT",
}

CRYPTO_TERMS = (
    "加密",
    "数字货币",
    "虚拟货币",
    "稳定币",
    "区块链",
    "币圈",
    "代币",
    "链上",
    "交易所",
    "钱包",
    "defi",
    "nft",
    "挖矿",
    "web3",
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "crypto",
    "stablecoin",
)

POLICY_TERMS = (
    "监管",
    "政策",
    "证监会",
    "央行",
    "国务院",
    "财政部",
    "美联储",
    "加息",
    "降息",
    "关税",
    "法案",
    "合规",
    "禁止",
    "处罚",
    "调查",
    "风险提示",
    "反洗钱",
    "牌照",
    "备案",
    "立法",
    "白宫",
    "特朗普",
    "sec",
    "监管机构",
    "央行数字货币",
    "cbdc",
)

NEGATIVE_TERMS = (
    "下跌",
    "暴跌",
    "大跌",
    "抛售",
    "清算",
    "破产",
    "违约",
    "黑客",
    "被盗",
    "攻击",
    "禁止",
    "处罚",
    "调查",
    "风险",
    "警告",
    "加息",
    "收紧",
    "制裁",
    "封锁",
    "下架",
    "裁员",
    "亏损",
    "爆仓",
    "砸盘",
    "利空",
    "崩盘",
    "闪崩",
)

POSITIVE_TERMS = (
    "上涨",
    "大涨",
    "突破",
    "创新高",
    "批准",
    "通过",
    "上市",
    "合作",
    "增持",
    "买入",
    "降息",
    "宽松",
    "利好",
    "反弹",
    "获批",
    "etf",
    "流入",
    "采用",
    "合法化",
)

EVENT_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("etf", ("etf", "基金", "信托")),
    ("security", ("黑客", "被盗", "攻击", "漏洞", "诈骗", "洗钱")),
    ("regulation", ("监管", "证监会", "sec", "法案", "合规", "牌照", "备案", "立法")),
    ("policy", ("政策", "央行", "国务院", "财政部", "美联储", "加息", "降息", "关税")),
    ("macro", ("gdp", "cpi", "非农", "通胀", "失业", "经济数据", "财报")),
    ("exchange", ("交易所", "上线", "下架", "暂停提现", "币安", "okx", "coinbase")),
)


def _has_term(low: str, term: str) -> bool:
    """中文按子串匹配；英文/数字按词边界匹配，避免 eth 命中 whether。"""
    needle = term.lower()
    if needle.isascii() and needle.isalnum():
        import re

        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", low) is not None
    return needle in low


def _contains(text: str, terms: Iterable[str]) -> list[str]:
    low = text.lower()
    return [term for term in terms if _has_term(low, term)]


def classify(
    text: str,
    *,
    default_market: str = "",
    default_symbols: Iterable[str] = (),
    force: bool = False,
) -> dict:
    """把新闻文本转成可入库的因子字段。

    ``force=True`` 用于加密新闻源（整站都是加密内容，不需要关键词才入库）。
    """
    text = (text or "").strip()
    low = text.lower()
    crypto_hits = _contains(text, CRYPTO_TERMS)
    policy_hits = _contains(text, POLICY_TERMS)
    neg_hits = _contains(text, NEGATIVE_TERMS)
    pos_hits = _contains(text, POSITIVE_TERMS)
    relevant = bool(crypto_hits or policy_hits or neg_hits or pos_hits) or force

    symbols: list[str] = []
    for term, symbol in CRYPTO_SYMBOLS.items():
        if _has_term(low, term):
            if symbol not in symbols:
                symbols.append(symbol)
    if not symbols:
        symbols = list(default_symbols)
    if not symbols and crypto_hits:
        market = "CRYPTO"
    elif not symbols and policy_hits:
        symbols = ["ALL"]
        market = ""
    else:
        market = default_market or ("CRYPTO" if crypto_hits else "")

    positive = sum(0.25 for _ in pos_hits) + sum(0.15 for _ in crypto_hits if "etf" in low)
    negative = sum(0.3 for _ in neg_hits)
    sentiment = (positive - negative) / max(1.0, positive + negative)
    sentiment = max(-1.0, min(1.0, sentiment))

    policy_weight = 0.0
    if policy_hits:
        policy_weight = min(0.95, 0.45 + 0.1 * len(policy_hits))
    if any(term in low for term in ("禁止", "处罚", "调查", "制裁", "封锁")):
        policy_weight = max(policy_weight, 0.85)
    policy_risk = policy_weight if policy_hits else 0.0

    event_type = "other"
    for name, terms in EVENT_TERMS:
        if any(term.lower() in low for term in terms):
            event_type = name
            break

    hits = len(set(crypto_hits + policy_hits + neg_hits + pos_hits))
    confidence = min(0.95, 0.35 + 0.1 * hits) if relevant else 0.2
    return {
        "relevant": relevant,
        "crypto_related": bool(crypto_hits),
        "policy_related": bool(policy_hits),
        "symbols": symbols,
        "market": market,
        "event_type": event_type,
        "sentiment": round(sentiment, 4),
        "policy_risk": round(policy_risk, 4),
        "confidence": round(confidence, 4),
    }


__all__ = ["CRYPTO_SYMBOLS", "classify"]
