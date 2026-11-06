"""规则型新闻分类/打分：无需外部模型，回测可复现。

新闻源只负责提供标题/正文；这里统一输出情绪、政策风险、事件类型、标的影响。

## 打分模型

旧版是"关键词计数"：每个正面词 +0.25、每个负面词 0.3，再按词数做归一化。
它只能回答"是利好还是利空"，**无法表达"影响多大"**——「降息 50 个基点」与
「上涨」两个字同分，「暴跌 5%」与「下跌」同分。且词表几乎只有中文，英文新闻
全部落成 0（实库里 170 条有 105 条英文，115 条 sentiment 恰好为 0）。

现在改成三层：

1. **强度词表**：每个词自带量级（1.0 / 0.8 / 0.55 / 0.3），同极性多个词按
   0.55 递减叠加，避免同义反复刷分；
2. **幅度放大**：从文本抽取百分比、基点、金额，换算成放大系数（跌 5% 比跌
   1% 更重）；极性方向最后由 ``tanh`` 压缩到 [-1, 1]，天然饱和不会越界；
3. **否定识别**：极性词前 10 个字符内出现否定词则该词不贡献极性——「不会立即
   影响关税协议」不该被判成高政策风险。

``policy_risk`` 也从"提到政策就算风险"改成看政策**动作方向**：禁止/处罚/调查
是高不确定性，批准/降息/合法化是政策明朗（低风险），一般提及给中间值。

``confidence`` 语义纠正为**文本可判断程度**（有具体数字、有明确主体则高；早报、
日历提醒这类汇编则低）。旧版把它算成"命中了几种词"，导致信息量最少的日历条目
拿到全库最高置信度，进而在因子聚合时权重最大。

后续接 LLM 时可以替换本模块，保持返回结构不变。
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

# --------------------------------------------------------------------------
# 标的识别
# --------------------------------------------------------------------------

# 币种关键词 → 规范标的
CRYPTO_SYMBOLS: dict[str, str] = {
    "比特币": "CRYPTO:BTC/USDT",
    "btc": "CRYPTO:BTC/USDT",
    "bitcoin": "CRYPTO:BTC/USDT",
    "以太坊": "CRYPTO:ETH/USDT",
    "ethereum": "CRYPTO:ETH/USDT",
    "ether": "CRYPTO:ETH/USDT",
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

# --------------------------------------------------------------------------
# 强度词表：把词按量级分组
# --------------------------------------------------------------------------
#
# 英文词以 ``*`` 结尾表示前缀匹配（investigat* → investigate / investigation），
# 非通配的词要求完整词边界，避免 eth 命中 whether。

NEGATIVE_LEXICON: tuple[tuple[float, tuple[str, ...]], ...] = (
    (
        1.0,
        (
            "崩盘",
            "闪崩",
            "归零",
            "破产",
            "清算",
            "爆仓",
            "全面禁止",
            "禁止交易",
            "禁止提现",
            "暂停提现",
            "封禁",
            "清退",
            "挤兑",
            "跑路",
            "资不抵债",
            "crash",
            "collapse",
            "bankrupt*",
            "insolven*",
            "halt*",
            "rug pull",
            "plunge*",
            "tumble*",
            "plummet*",
        ),
    ),
    (
        0.8,
        (
            "暴跌",
            "大跌",
            "重挫",
            "跳水",
            "血洗",
            "抛售",
            "被盗",
            "黑客",
            "攻击",
            "制裁",
            "封锁",
            "下架",
            "禁止",
            "查封",
            "立案",
            "逮捕",
            "起诉",
            "违约",
            "开战",
            "空袭",
            "入侵",
            "军事行动",
            "宣战",
            "上调关税",
            "加征关税",
            "下调评级",
            "下调目标价",
            "meltdown",
            "跌破",
            "falls below",
            "drops below",
            "plunges below",
            "worst",
            "worse",
            "slump*",
            "selloff",
            "sell-off",
            "hack*",
            "exploit*",
            "ban",
            "banned",
            "sanction*",
            "delist*",
            "lawsuit",
            "arrest*",
        ),
    ),
    (
        0.55,
        (
            "下跌",
            "回落",
            "走低",
            "承压",
            "调查",
            "处罚",
            "罚款",
            "加息",
            "收紧",
            "裁员",
            "亏损",
            "预警",
            "风险提示",
            "利空",
            "战争",
            "冲突",
            "地缘政治",
            "紧张局势",
            "下调预期",
            "下调预测",
            "成本上升",
            "利润率下降",
            "drop",
            "drops",
            "declin*",
            "fall*",
            "fell",
            "war",
            "conflict*",
            "escalat*",
            "military",
            "downgrade*",
            "shrink*",
            "probe*",
            "investigat*",
            "fine",
            "fined",
            "penalt*",
            "hike",
            "hikes",
            "layoff*",
            "warn*",
            "bearish",
        ),
    ),
    (
        0.3,
        (
            "回调",
            "调整",
            "疲软",
            "谨慎",
            "担忧",
            "不确定",
            "波动",
            "震荡",
            "pressure",
            "dip",
            "dips",
            "pullback",
            "weak*",
            "caution*",
            "concern*",
            "uncertain*",
            "volatil*",
        ),
    ),
)

POSITIVE_LEXICON: tuple[tuple[float, tuple[str, ...]], ...] = (
    (
        1.0,
        (
            "现货etf获批",
            "正式批准",
            "合法化",
            "全面合法",
            "机构入场",
            "主权基金",
            "降息",
            "rate cut",
            "rate cuts",
            "spot etf approv*",
            "full legaliz*",
        ),
    ),
    (
        0.8,
        (
            "获批",
            "批准",
            "通过",
            "创新高",
            "历史新高",
            "大涨",
            "暴涨",
            "飙升",
            "宽松",
            "利好",
            "增持",
            "回购",
            "上市",
            "approv*",
            "greenlight*",
            "record high",
            "bullish",
            "buyback*",
        ),
    ),
    (
        0.55,
        (
            "上涨",
            "反弹",
            "突破",
            "流入",
            "采用",
            "减半",
            "中标",
            "放宽",
            "放松",
            "surge*",
            "soar*",
            "rally",
            "rallies",
            "gain",
            "gains",
            "rise",
            "rises",
            "climb*",
            "partnership*",
            "inflow*",
            "adopt*",
            "upgrade*",
        ),
    ),
    (
        0.3,
        (
            "回暖",
            "企稳",
            "看好",
            "乐观",
            "改善",
            "复苏",
            "合作",
            "win",
            "wins",
            "won",
            "reelect*",
            "recover*",
            "stabiliz*",
            "optimis*",
            "improv*",
        ),
    ),
)

# --------------------------------------------------------------------------
# 政策动作：区分"打压性"与"支持性"
# --------------------------------------------------------------------------

POLICY_RESTRICTIVE = (
    "禁止",
    "禁令",
    "处罚",
    "罚款",
    "调查",
    "立案",
    "制裁",
    "封锁",
    "查处",
    "约谈",
    "整改",
    "暂停",
    "下架",
    "加征",
    "上调关税",
    "收紧",
    "限制",
    "打击",
    "清退",
    "反洗钱",
    "ban",
    "banned",
    "sanction*",
    "crackdown",
    "prosecut*",
    "penalt*",
)

POLICY_SUPPORTIVE = (
    "批准",
    "获批",
    "通过",
    "合法化",
    "宽松",
    "降息",
    "减税",
    "补贴",
    "支持",
    "鼓励",
    "试点",
    "开放",
    "发牌",
    "approv*",
    "legaliz*",
    "rate cut",
    "stimulus",
    "deregulat*",
)

POLICY_NEUTRAL = (
    "监管",
    "政策",
    "证监会",
    "央行",
    "国务院",
    "财政部",
    "美联储",
    "法案",
    "合规",
    "牌照",
    "备案",
    "立法",
    "议会",
    "白宫",
    "关税",
    "sec",
    "监管机构",
    "央行数字货币",
    "cbdc",
    "regulation",
    "regulator*",
    "treasury",
    "congress",
    "parliament",
    "tariff*",
)

# --------------------------------------------------------------------------
# 否定与条件
# --------------------------------------------------------------------------

NEGATION_TERMS = (
    "不",
    "未",
    "没",
    "无",
    "非",
    "否认",
    "辟谣",
    "排除",
    "驳斥",
    "不会",
    "未能",
    "尚未",
    "并未",
    "不再",
    "不至于",
    "not",
    "no",
    "never",
    "without",
    "deny",
    "denies",
    "denied",
    "unlikely",
    "wont",
    "won't",
    "doesnt",
    "doesn't",
    "isnt",
    "isn't",
    "fails",
    "failed to",
    "reject*",
    "refuse*",
    "ruled out",
)

# 「不但」「不仅」里的「不」不是否定，别误伤；「未来」「非农」「无论」同理。
# 「不能排除」是双重否定=肯定，若当成否定会把「不能排除大跌」判成利好。
_FALSE_NEGATION = (
    "不但",
    "不仅",
    "不止",
    "不断",
    "不错",
    "不久",
    "未来",
    "非农",
    "无论",
    "除了",
    "不能排除",
    "不排除",
    "无法排除",
    "难以排除",
    "不可避免",
    "not only",
    "no doubt",
    "cannot rule out",
    "can't rule out",
)

_DENIAL_VERBS = ("deny", "denies", "denied", "reject*", "refuse*", "驳回", "拒绝", "不予")
_DENIAL_TARGETS = (
    "application",
    "request",
    "proposal",
    "petition",
    "申请",
    "提案",
    "请求",
    "牌照",
    "许可",
    "etf",
)

_NEG_WINDOW_CN = 10
_NEG_WINDOW_EN = 34

# 中文的否定常落在词**后面**（「关税不会影响…」），前置窗口抓不到
_POST_NEGATION = (
    "不会影响",
    "不受影响",
    "未受影响",
    "无影响",
    "不影响",
    "不构成影响",
    "不会波及",
    "不会改变",
    "不会立即影响",
)

# 强否认：与"不/未"这类一般否定不同，它明确推翻了此前的利空传闻，
# 属于"利空落空"，本身是轻微利好（「监管否认将禁止加密货币交易」）。
_DENIAL_TERMS = (
    "否认",
    "辟谣",
    "排除",
    "驳斥",
    "不实",
    "谣言",
    "澄清",
    "ruled out",
    "deny",
    "denies",
    "denied",
    "refute*",
    "dismiss*",
    "debunk*",
)

# --------------------------------------------------------------------------
# 幅度抽取
# --------------------------------------------------------------------------

_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|％|个百分点|percent|pct)")
_BP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:个?基点|bps?|basis points?)")
_MONEY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:亿|万|million|billion|mn|bn|trillion)\s*(?:美元|美金|元|usd|dollars?)?",
    re.IGNORECASE,
)
_HAS_DIGIT_RE = re.compile(r"\d")

_AMPLITUDE_CAP = 3.0

# 削弱程度词：「小幅回调」和「回调」不该同分
_DAMPENERS = (
    "小幅",
    "轻微",
    "略微",
    "略",
    "微幅",
    "小跌",
    "小涨",
    "slightly",
    "marginal*",
    "modest*",
    "small",
    "tiny",
    "minor",
)

# --------------------------------------------------------------------------
# 事件模板
# --------------------------------------------------------------------------

EVENT_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("etf", ("现货etf", "etf获批", "etf通过", "spot etf", "bitcoin etf", "信托", "etf")),
    (
        "security",
        ("黑客", "被盗", "攻击", "漏洞", "诈骗", "洗钱", "exploit*", "hack*", "breach", "stolen"),
    ),
    (
        "regulation",
        (
            "监管",
            "证监会",
            "sec",
            "法案",
            "合规",
            "牌照",
            "备案",
            "立法",
            "起诉",
            "判决",
            "调查",
            "处罚",
            "regulator*",
            "lawsuit",
        ),
    ),
    (
        "policy",
        ("政策", "央行", "国务院", "财政部", "美联储", "加息", "降息", "关税", "货币政策"),
    ),
    ("macro", ("gdp", "cpi", "非农", "通胀", "失业", "经济数据", "利率决议", "pmi")),
    (
        "exchange",
        ("交易所", "上线", "下架", "暂停提现", "币安", "okx", "coinbase", "binance", "exchange"),
    ),
    ("market", ("收盘", "开盘", "指数", "行情", "rally", "selloff", "stocks", "market")),
)

# 所有带极性的词（用于判断"是否相关"）
_POLAR_TERMS: tuple[str, ...] = tuple(
    term for _, group in (*POSITIVE_LEXICON, *NEGATIVE_LEXICON) for term in group
)

# 里程碑组合：单独看每个词都不算极端，合起来才是分水岭级别的事件。
# 「SEC 批准现货比特币 ETF」里的 approv* 与普通「批准某合作」同权重，
# 必须靠组合加成把它抬到应有的量级。
_MILESTONE_BONUSES: tuple[tuple[tuple[str, ...], tuple[str, ...], float], ...] = (
    (("etf",), ("approv*", "greenlight*", "获批", "批准", "通过"), 0.45),
    (("现货", "spot"), ("etf",), 0.15),
)

# 汇编/日历类文本：信息密度低，应压低置信度
_DIGEST_TERMS = (
    "早餐",
    "早报",
    "晚报",
    "日报",
    "周报",
    "盘点",
    "汇总",
    "一览",
    "速览",
    "要闻",
    "重点关注",
    "今日看点",
    "财经日历",
    "收盘播报",
    "盘中异动",
    "digest",
    "roundup",
    "what happened",
    "live updates",
    "recap",
)

# 明确主体：有主语才谈得上"这条新闻在说谁"
_ENTITY_TERMS = (
    "公司",
    "集团",
    "银行",
    "交易所",
    "基金",
    "证券",
    "委员会",
    "政府",
    "部门",
    "央行",
    "美联储",
    "证监会",
    "美国",
    "中国",
    "日本",
    "韩国",
    "欧洲",
    "欧盟",
    "sec",
    "fed",
    "treasury",
    "congress",
    "inc",
    "corp",
    "ltd",
)


def _has_term(low: str, term: str) -> bool:
    """中文按子串匹配；英文按词边界匹配（支持 ``*`` 前缀通配）。"""
    needle = term.lower()
    if needle.endswith("*"):
        base = needle[:-1]
        if not base:
            return False
        return re.search(rf"(?<![a-z0-9]){re.escape(base)}[a-z]*", low) is not None
    if needle.isascii() and needle.replace(" ", "").isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", low) is not None
    return needle in low


def _find_pos(low: str, term: str) -> int:
    """返回词在文本中的首个起始位置，未命中返回 -1。

    英文必须走词边界正则，否则 ``drop`` 会命中 ``drops``、``fall`` 会命中
    ``fallout``，同一条新闻被重复计分。
    """
    needle = term.lower()
    if needle.endswith("*"):
        base = needle[:-1]
        if not base:
            return -1
        match = re.search(rf"(?<![a-z0-9]){re.escape(base)}[a-z]*", low)
        return match.start() if match else -1
    if needle.isascii() and needle.replace(" ", "").isalnum():
        match = re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", low)
        return match.start() if match else -1
    return low.find(needle)


def _contains(text: str, terms: Iterable[str]) -> list[str]:
    low = text.lower()
    return [term for term in terms if _has_term(low, term)]


def _negated_at(low: str, pos: int, span: int = 1) -> bool:
    """极性词前后一小段里是否出现否定。

    ``span`` 是被匹配词自身的长度，用于检查词**之后**的后置否定。
    """
    if pos < 0:
        return False
    tail = low[pos + span : pos + span + 16]
    if any(phrase in tail for phrase in _POST_NEGATION):
        return True
    if pos == 0:
        return False
    window_cn = low[max(0, pos - _NEG_WINDOW_CN) : pos]
    for phrase in _FALSE_NEGATION:
        window_cn = window_cn.replace(phrase, "")
    if any(not term.isascii() and term in window_cn for term in NEGATION_TERMS):
        return True
    window_en = low[max(0, pos - _NEG_WINDOW_EN) : pos]
    return any(term.isascii() and _has_term(window_en, term) for term in NEGATION_TERMS)


def _weighted_hits(
    low: str, lexicon: tuple[tuple[float, tuple[str, ...]], ...]
) -> tuple[list[float], list[float]]:
    """返回 (未被否定的强度列表, 被否定的强度列表)。"""
    live: list[float] = []
    negated: list[float] = []
    for weight, terms in lexicon:
        for term in terms:
            pos = _find_pos(low, term)
            if pos < 0:
                continue
            if _negated_at(low, pos, len(term)):
                negated.append(weight)
            else:
                live.append(weight)
    return live, negated


def _blend(weights: list[float]) -> float:
    """同极性多词递减叠加：最强的全额计入，其余按 0.55 衰减。"""
    if not weights:
        return 0.0
    total = 0.0
    factor = 1.0
    for weight in sorted(weights, reverse=True):
        total += weight * factor
        factor *= 0.45
    return total


def _amplitude(text: str) -> float:
    """把文本里的幅度数字换算成放大系数（1.0 起步，上限 3.0）。"""
    amp = 1.0
    for raw in _PCT_RE.findall(text):
        value = abs(float(raw))
        amp = max(amp, 1.0 + 0.15 * min(value, 20.0))
    for raw in _BP_RE.findall(text):
        value = abs(float(raw))
        amp = max(amp, 1.0 + 0.012 * min(value, 100.0))
    for raw in _MONEY_RE.findall(text):
        value = abs(float(raw))
        if value >= 100:
            amp = max(amp, 1.8)
        elif value >= 10:
            amp = max(amp, 1.5)
        elif value >= 1:
            amp = max(amp, 1.25)
    return min(_AMPLITUDE_CAP, amp)


def _milestone_bonus(low: str) -> float:
    bonus = 0.0
    for markers, with_terms, amount in _MILESTONE_BONUSES:
        if any(_has_term(low, item) for item in markers) and any(_has_term(low, item) for item in with_terms):
            bonus += amount
    return bonus


def _denied_request(low: str) -> bool:
    """「SEC 驳回 ETF 申请」这类：拒绝动作 + 申请对象，是明确利空。

    ``deny`` 一般作否定词用（"denies reports"），只有否定的是申请/牌照时才成为事件本身。
    """
    for verb in _DENIAL_VERBS:
        pos = _find_pos(low, verb)
        if pos < 0:
            continue
        tail = low[pos : pos + 60]
        if any(_has_term(tail, target) for target in _DENIAL_TARGETS):
            return True
    return False


def _policy_risk(low: str) -> float:
    """政策风险 = 政策动作的不利程度，而不是"有没有提到政策"。"""
    restrictive = [term for term in POLICY_RESTRICTIVE if _has_term(low, term)]
    supportive = [term for term in POLICY_SUPPORTIVE if _has_term(low, term)]
    neutral = [term for term in POLICY_NEUTRAL if _has_term(low, term)]

    # 被否定的政策动作不计强度：「不会加征关税」不该是高危
    hard = [term for term in restrictive if not _negated_at(low, _find_pos(low, term), len(term))]
    soft = [term for term in supportive if not _negated_at(low, _find_pos(low, term), len(term))]

    if hard:
        risk = 0.72 + 0.06 * (len(hard) - 1)
        if soft:
            risk *= 0.85  # 同文里出现对冲性表态，方向不够纯粹
        return min(0.95, risk)
    if soft:
        # 政策方向明朗且偏支持：不确定性解除，但仍算政策事件
        return min(0.30, 0.12 + 0.04 * (len(soft) - 1))
    if neutral:
        live = [term for term in neutral if not _negated_at(low, _find_pos(low, term), len(term))]
        if not live:
            return 0.0
        return min(0.60, 0.35 + 0.05 * (len(live) - 1))
    return 0.0


def _confidence(text: str, *, relevant: bool, negated: bool) -> float:
    """文本可判断程度：有事实、有主体则高，汇编/日历类则低。"""
    if not relevant:
        return 0.2
    score = 0.42
    if _HAS_DIGIT_RE.search(text):
        score += 0.18
    if _contains(text, _ENTITY_TERMS):
        score += 0.12
    if len(text) >= 24:
        score += 0.05
    if _contains(text, _DIGEST_TERMS):
        score -= 0.40
    if negated:
        score -= 0.05
    return max(0.1, min(0.95, score))


def _event_type(low: str) -> str:
    """按命中数打分选事件类型，而不是固定顺序的首个命中。"""
    best = "other"
    best_hits = 0
    for name, terms in EVENT_TERMS:
        hits = sum(1 for term in terms if _has_term(low, term))
        if hits > best_hits:
            best = name
            best_hits = hits
    return best


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
    policy_hits = _contains(text, POLICY_NEUTRAL + POLICY_RESTRICTIVE + POLICY_SUPPORTIVE)
    relevant = bool(crypto_hits or policy_hits or _contains(text, _POLAR_TERMS)) or force

    symbols: list[str] = []
    for term, symbol in CRYPTO_SYMBOLS.items():
        if _has_term(low, term) and symbol not in symbols:
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

    pos_live, pos_negated = _weighted_hits(low, POSITIVE_LEXICON)
    neg_live, neg_negated = _weighted_hits(low, NEGATIVE_LEXICON)

    # 否定做方向翻转，而不是直接归零：「美联储不会降息」是利空，
    # 「央行未加息」是轻微利好。翻转强度弱于直接命中，因为语气更弱。
    positive = _blend(pos_live) + _blend(neg_negated) * 0.35
    negative = _blend(neg_live) + _blend(pos_negated) * 0.75
    if _denied_request(low):
        negative += 0.7
    raw = positive - negative

    # 强否认在案的利空政策 = 利空落空，给一点正分并把政策风险压下去
    denied_policy = [
        term
        for term in POLICY_RESTRICTIVE
        if _has_term(low, term) and _negated_at(low, _find_pos(low, term), len(term))
    ]
    strong_denial = bool(denied_policy) and bool(_contains(text, _DENIAL_TERMS))
    if strong_denial:
        raw = max(raw, 0.25)

    if raw > 0:
        raw += _milestone_bonus(low)
    if _contains(text, _DAMPENERS):
        raw *= 0.6
    amplitude = _amplitude(text)
    # 幅度取平方根：跌 20% 不该是跌 1% 的 20 倍影响，而是约 4.5 倍
    sentiment = math.tanh(0.9 * raw * math.sqrt(amplitude)) if raw else 0.0
    sentiment = max(-0.97, min(0.97, sentiment))

    policy_risk = _policy_risk(low)
    if strong_denial:
        policy_risk *= 0.35
    confidence = _confidence(
        text, relevant=relevant, negated=bool(pos_negated or neg_negated or _denied_request(low))
    )
    event_type = _event_type(low)

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
