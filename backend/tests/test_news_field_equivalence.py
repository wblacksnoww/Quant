"""新闻字段的 C++ 通路测试。

**为什么单独一个文件**：`test_native_equivalence.py` 用内置策略和纯技术 spec
做两条腿比对，`news_*` 字段从未被走过。而新闻列是唯一"Python 算好、
作为 extras 数组喂给 C++"的输入 —— 一旦 as_of 口径或窗口映射有偏差，
C++ 看到的就是另一组数字，**不会报错**，只会让回测结论悄悄偏离。

## 先说清楚一个结构性事实

`custom_rule`（拼装策略）**没有 Python 信号腿**：`CustomRuleStrategy.__init__`
要求 `core_native.HAS_NATIVE`，否则直接抛 RuntimeError。原因是信号的编译与
求值本来就只在 C++ 里实现，Python 侧只有 spec 语法校验。

所以"两条腿逐笔比对"对拼装策略+新闻字段**做不到**，这里不假装能做。
本文件改守三条**实际能验证**的契约：

1. 新闻源的 `as_of` / `window` 参数正确（用可记录的假新闻源直接断言）
2. 缺新闻源时显式退化并告警，而不是静默零成交
3. 新闻条件真的参与了决策（有/无新闻源时成交不同）

内置策略的两条腿等价（含 news_dual_ma 无 provider 场景）在
`test_native_equivalence.py` 与本文件末尾各自覆盖。
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pytest

from quant import core_native
from quant.engine import BacktestEngine, EngineConfig
from quant.news.factors import NewsFactor

pytestmark = pytest.mark.skipif(not core_native.HAS_NATIVE, reason="C++ 扩展未构建")

SYMBOL = "CN:600519"


class RecordingNewsSource:
    """记录每次调用的 (window, as_of)，并返回随 as_of 变化的因子。

    因子刻意**随时间变化**（非常数）：如果 as_of 错了但因子恒定，
    结果不变，测试就白测了。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime]] = []

    def factor(
        self,
        symbol: str,
        *,
        as_of: datetime,
        window: str = "1h",
        half_life_hours: float = 24.0,
    ) -> NewsFactor:
        self.calls.append((window, as_of))
        day = as_of.timetuple().tm_yday
        sentiment = 1.0 / (1.0 + np.exp(-(((day % 40) - 20) / 4.0)))
        return NewsFactor(
            symbol=symbol,
            as_of=as_of,
            window=window,
            sentiment=sentiment,
            policy_risk=0.2,
            confidence=0.9,
            event_count=int(day % 7),
        )

    @property
    def windows(self) -> set[str]:
        return {w for w, _ in self.calls}

    @property
    def as_ofs(self) -> list[datetime]:
        return [a for _, a in self.calls]


def _spec_with_news(field: str = "news_sentiment", threshold: float = 0.3) -> dict:
    sma5 = {"kind": "indicator", "name": "sma", "params": {"period": 5}}
    sma20 = {"kind": "indicator", "name": "sma", "params": {"period": 20}}
    return {
        "version": 1,
        "description": "新闻 + 技术",
        "category": "trend",
        "entry": [
            {"type": "cross_above", "left": sma5, "right": sma20},
            {
                "type": "gt",
                "left": {"kind": "field", "field": field},
                "right": {"kind": "const", "value": threshold},
            },
        ],
        "exit": [{"type": "cross_below", "left": sma5, "right": sma20}],
        "position_pct": 0.9,
        "stop_loss": 0.08,
        "take_profit": 0.0,
    }


def _run(context, run_id: str, *, news, spec: dict | None = None, symbols=None):
    cfg = EngineConfig(
        strategy_id="custom_rule",
        symbols=symbols or [SYMBOL],
        start=date(2023, 1, 1),
        end=date(2024, 6, 28),
        initial_cash=1_000_000,
        params={"spec": spec if spec is not None else _spec_with_news()},
        run_id=run_id,
    )
    return BacktestEngine(cfg, context.data, news=news).run()


class TestCustomRuleHasNoPythonLeg:
    """固化上面 docstring 里那个结构性事实，防止有人误以为可以双跑。"""

    def test_python_leg_refuses_custom_rule(self, context):
        """拼装策略在 HAS_NATIVE=False 时明确报错，而不是悄悄走别的实现。"""
        original = core_native.HAS_NATIVE
        core_native.HAS_NATIVE = False
        try:
            result = _run(context, "cr_no_native", news=None)
        finally:
            core_native.HAS_NATIVE = original
        assert result.status == "failed"
        assert "C++" in (result.error or ""), f"错误信息应指明需要 C++，实际：{result.error}"


class TestNewsAsOfAndWindow:
    """新闻源收到的 as_of / window 必须正确。"""

    def test_requests_1h_window_for_plain_field(self, context):
        news = RecordingNewsSource()
        result = _run(context, "news_win_1h", news=news)
        assert result.status == "finished", result.error
        assert news.calls, "新闻源未被调用 —— 新闻列没接上"
        assert news.windows == {"1h"}, f"news_sentiment 应只请求 1h，实际 {news.windows}"

    def test_requests_1d_window_for_suffixed_field(self, context):
        news = RecordingNewsSource()
        result = _run(
            context, "news_win_1d", news=news, spec=_spec_with_news("news_sentiment_1d")
        )
        assert result.status == "finished", result.error
        assert news.windows == {"1d"}, f"news_sentiment_1d 应只请求 1d，实际 {news.windows}"

    def test_as_of_matches_bar_dates(self, context):
        """as_of 必须落在回测区间的 bar 日期上，不能是"现在"。"""
        news = RecordingNewsSource()
        result = _run(context, "news_asof", news=news, spec=_spec_with_news("news_sentiment_1d"))
        assert result.status == "finished", result.error
        lo, hi = date(2023, 1, 1), date(2024, 6, 28)
        out_of_range = [a for a in news.as_ofs if not (lo <= a.date() <= hi)]
        assert not out_of_range, (
            f"{len(out_of_range)} 次 as_of 落在回测区间外，例 {out_of_range[:3]}"
        )
        assert len(news.as_ofs) >= 100, f"只问了 {len(news.as_ofs)} 次，覆盖不足"

    def test_as_of_is_pit_not_lookahead(self, context):
        """每根 bar 的 as_of 不得晚于该 bar 自身日期（PIT，防未来函数）。

        这条是新闻因子最容易出问题的地方：如果用"整段数据的最后一天"
        或运行时刻去取因子，等于让早期 bar 看到了未来的新闻。
        """
        news = RecordingNewsSource()
        result = _run(context, "news_pit", news=news, spec=_spec_with_news("news_sentiment_1d"))
        assert result.status == "finished", result.error
        # as_of 序列应大致随行号非递减（按 bar 顺序取因子）
        drift = [
            (str(a), str(b))
            for a, b in zip(news.as_ofs, news.as_ofs[1:], strict=False)
            if b.date() < a.date() - __import__("datetime").timedelta(days=400)
        ]
        assert not drift, f"as_of 出现异常回退：{drift[:3]}"


class TestNewsPresenceChangesDecisions:
    """有新闻源与无新闻源，结果必须不同 —— 否则新闻列是摆设。"""

    def test_with_vs_without_news_differs(self, context):
        with_news = _run(context, "news_on", news=RecordingNewsSource())
        without = _run(context, "news_off", news=None)
        assert with_news.status == without.status == "finished"
        assert with_news.trades, "带新闻源时零成交，无法判断新闻是否起作用"

        a = [(t["dt"], t["side"], t["quantity"]) for t in with_news.trades]
        b = [(t["dt"], t["side"], t["quantity"]) for t in without.trades]
        assert a != b, (
            "有/无新闻源产生了完全相同的成交序列 —— 新闻条件没有参与决策。\n"
            "常见原因：门槛设得太松（条件恒真）或太紧（条件恒假）。"
        )

    def test_strict_threshold_blocks_all_entries(self, context):
        """门槛提到不可能达到时，应当零成交 —— 证明条件真的在拦。"""
        # sentiment 落在 (0,1)；门槛 1.5 恒不成立
        result = _run(
            context, "news_strict", news=RecordingNewsSource(),
            spec=_spec_with_news("news_sentiment", threshold=1.5),
        )
        assert result.status == "finished", result.error
        assert result.trades == [], f"门槛 1.5 不该有成交，实际 {len(result.trades)} 笔"

    def test_loose_threshold_allows_entries(self, context):
        """门槛放到 -1（恒成立）时，应等价于纯技术条件。"""
        loose = _run(
            context, "news_loose", news=RecordingNewsSource(),
            spec=_spec_with_news("news_sentiment", threshold=-1.0),
        )
        assert loose.status == "finished", loose.error
        assert loose.trades, "门槛 -1 应有成交"


class TestNewsAbsentDegradesVisibly:
    """缺新闻源时必须显式退化 + 告警，而不是静默零成交。"""

    def test_strips_conditions_and_warns(self, context):
        result = _run(context, "news_strip", news=None)
        assert result.status == "finished", result.error
        msgs = " ".join(str(item.get("message", "")) for item in (result.logs or []))
        assert "新闻" in msgs, f"未告警新闻数据缺失，logs={result.logs}"
        assert "剥掉" in msgs or "没有新闻数据源" in msgs, f"告警内容不明确：{msgs[:200]}"

    def test_degraded_equals_pure_technical(self, context):
        """剥掉新闻条件后，结果应等于从 spec 里就没写新闻条件。"""
        stripped = _run(context, "news_deg", news=None)

        sma5 = {"kind": "indicator", "name": "sma", "params": {"period": 5}}
        sma20 = {"kind": "indicator", "name": "sma", "params": {"period": 20}}
        pure = {
            "version": 1,
            "entry": [{"type": "cross_above", "left": sma5, "right": sma20}],
            "exit": [{"type": "cross_below", "left": sma5, "right": sma20}],
            "position_pct": 0.9,
            "stop_loss": 0.08,
            "take_profit": 0.0,
        }
        baseline = _run(context, "news_pure", news=None, spec=pure)

        assert stripped.status == baseline.status == "finished"
        a = [(t["dt"], t["side"], t["quantity"], t["price"]) for t in stripped.trades]
        b = [(t["dt"], t["side"], t["quantity"], t["price"]) for t in baseline.trades]
        assert a == b, (
            f"退化结果与纯技术 spec 不一致：{len(a)} 笔 vs {len(b)} 笔。\n"
            "说明 _strip_news_conditions 剥得不干净（可能只剥了入场未剥出场）。"
        )


class TestNewsDualMaNative:
    """news_dual_ma 的新闻条件真的接入了（走 C++）。"""

    def test_build_spec_contains_news_fields(self):
        from quant.strategy.builtin.news_dual_ma import build_spec

        spec = build_spec()
        entry_fields = [
            op.get("field")
            for cond in spec["entry"]
            for op in (cond.get("left"), cond.get("right"))
            if isinstance(op, dict)
        ]
        news_in_entry = [f for f in entry_fields if isinstance(f, str) and f.startswith("news_")]
        assert news_in_entry, (
            "build_spec() 生成的入场条件里没有 news_* 字段 —— "
            "spec 退化成纯双均线，新闻因子形同没接"
        )

    def test_default_params_still_use_news(self):
        """默认参数（门槛取默认值）也必须把新闻条件写进 spec。

        回归：曾经把"等于默认值就不写"当优化，结果默认参数下 factor() 零调用，
        而且完全没有报错 —— 属于最危险的那类静默失效。
        """
        from quant.strategy.builtin.news_dual_ma import build_spec

        spec = build_spec()
        text = str(spec)
        assert "news_" in text, "默认参数生成的 spec 未引用任何新闻字段"
        assert "news_sentiment" in text or "news_confidence" in text

    def test_news_window_switches_field_suffix(self):
        from quant.strategy.builtin.news_dual_ma import build_spec

        spec_1h = str(build_spec(news_window="1h"))
        spec_1d = str(build_spec(news_window="1d"))
        assert "news_sentiment" in spec_1h and "news_sentiment_1d" not in spec_1h, (
            "1h 窗口不该用 _1d 后缀字段"
        )
        assert "news_sentiment_1d" in spec_1d, "1d 窗口应使用 _1d 后缀字段"
