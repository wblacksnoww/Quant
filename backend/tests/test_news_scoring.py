"""新闻打分器的标定测试。

这里断言的是**语义方向与相对量级**，不锁死具体小数——权重会随词表调整，
但「跌 5% 不该是中性」「不会降息该是利空」「日历提醒不该有最高置信度」
这些性质一旦回退就是真 bug。
"""

from __future__ import annotations

import pytest

from quant.news.classifier import classify


def _sent(text: str) -> float:
    return classify(text)["sentiment"]


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Live updates: Bitcoin drops more than 5% as stocks close lower", "英文下跌"),
        ("Bitcoin tumbles to $76,000 as collapsing AI stocks drag risk assets down", "英文暴跌"),
        ("Wall Street slumps as risk assets sell off", "英文抛售"),
    ],
)
def test_english_negative_news_is_not_neutral(text: str, label: str) -> None:
    """旧版词表只有中文，英文下跌新闻全部落成 0.0（实库 68% 的条目如此）。"""
    assert _sent(text) < -0.3, f"{label} 应判为明确利空，实际 {_sent(text)}"


def test_english_positive_news_is_not_neutral() -> None:
    assert _sent("Crypto ally wins reelection in Congress") > 0.15


def test_amplitude_separates_severity() -> None:
    """同样是「上涨」，幅度不同得分必须不同——旧版完全等价。"""
    small = _sent("比特币上涨1%")
    big = _sent("比特币上涨30%")
    assert 0 < small < big
    assert big - small > 0.15, f"1% 与 30% 的差值只有 {big - small:.2f}，量级没体现出来"


def test_dampener_reduces_mild_move() -> None:
    assert _sent("黄金小幅回调") > _sent("黄金回调")
    assert _sent("黄金小幅回调") < 0


def test_crash_outranks_plain_decline() -> None:
    assert _sent("比特币崩盘，全网爆仓200亿美元") < _sent("比特币下跌")


def test_negation_flips_direction() -> None:
    """旧版不识别否定：「美联储不会降息」会因为命中「降息」被判成利好。"""
    assert _sent("美联储不会降息，市场预期落空") < -0.2
    # 否定利空 = 利空落空，轻微偏正
    assert _sent("央行未加息，维持利率不变") > 0


def test_denied_ban_is_mildly_positive() -> None:
    """「监管否认将禁止交易」是利空落空，不该被判成高政策风险。"""
    result = classify("监管否认将禁止加密货币交易")
    assert result["sentiment"] > 0
    assert result["policy_risk"] < 0.2


def test_double_negative_keeps_direction() -> None:
    """「不能排除大跌」是双重否定=肯定会跌，不能因为看到「不」就翻转成利好。"""
    assert _sent("不能排除美股大跌对全球市场的溢出效应") < -0.3
    assert _sent("无法排除进一步下跌的可能") < -0.3


def test_geopolitical_risk_is_negative() -> None:
    """战争/军事行动这类地缘事件旧版完全没有词表，一律给 0。"""
    assert _sent("美国总统：很快决定是恢复针对伊朗的军事行动") < -0.3
    assert _sent("地区冲突升级，市场避险情绪升温") < -0.2


def test_rejected_application_is_negative() -> None:
    """deny 一般作否定词，但「驳回申请」本身就是利空事件。"""
    assert _sent("SEC denies Bitcoin ETF application") < -0.3


@pytest.mark.parametrize(
    "text",
    [
        "日本经济产业大臣：美国的选举不会立即影响到关税协议",
        "美联储：关税不会影响当前货币政策路径",
    ],
)
def test_negated_policy_mention_has_no_risk(text: str) -> None:
    """「不会影响关税协议」曾因命中「关税」被判 policy_risk 0.55。"""
    assert classify(text)["policy_risk"] < 0.2


def test_supportive_policy_is_not_a_risk() -> None:
    """降息 / 批准是政策明朗，不该被当成政策风险。"""
    for text in ("美联储宣布降息50个基点", "SEC approves spot Bitcoin ETF"):
        result = classify(text)
        assert result["policy_risk"] < 0.3, f"{text} 的 policy_risk={result['policy_risk']}"
        assert result["sentiment"] > 0.5


def test_restrictive_policy_is_high_risk() -> None:
    result = classify("证监会立案调查某上市公司财务造假")
    assert result["policy_risk"] >= 0.6
    assert result["sentiment"] < 0


def test_digest_has_low_confidence() -> None:
    """旧版把 confidence 算成"命中几种词"，日历提醒因此拿到全库最高的 0.95。"""
    digest = classify("提醒：日内请重点关注（以下均为北京时间）")
    real = classify("美联储宣布降息50个基点，为2020年以来最大幅度")
    assert digest["confidence"] < 0.35
    assert real["confidence"] > digest["confidence"] + 0.2


def test_milestone_etf_approval_outranks_plain_approval() -> None:
    """「批准现货 ETF」是分水岭事件，不能与普通「批准合作」同分。"""
    milestone = _sent("SEC approves spot Bitcoin ETF for institutional investors")
    plain = _sent("公司批准一项合作")
    assert milestone > plain + 0.15, f"里程碑 {milestone:.2f} 与普通批准 {plain:.2f} 拉不开"
    assert milestone > 0.75


def test_event_type_uses_best_match_not_first_hit() -> None:
    """旧版按固定顺序首个命中：含「基金」的新闻一律归为 etf。"""
    assert classify("原土耳其部长在基金行业调查中被逮捕")["event_type"] == "regulation"
    assert classify("SEC approves spot Bitcoin ETF")["event_type"] == "etf"


def test_sentiment_stays_in_range() -> None:
    for text in (
        "比特币崩盘，全网爆仓200亿美元，交易所暂停提现",
        "比特币暴涨50%，突破10万美元创历史新高，机构大幅增持",
    ):
        assert -1.0 <= _sent(text) <= 1.0
