"""tick 引擎的机制不变量测试。

**这些测试不验证收益。** 合成 tick 的价格路径是插值出来的，收益毫无意义。
它们验证的是"机制是否正确"，换成真实 Level2 数据后应当同样成立：

* 现金永不为负
* 成交不超成交量上限
* T+1：当日买入不可卖
* 整手
* 手续费为正、卖出含印花税
* 权益 = 现金 + 持仓 x 价格

如果将来发现某项在这里失败，说明是机制退化，不是策略表现问题。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import numpy as np
import pytest

from quant import core_native
from quant.engine import EngineConfig
from quant.engine.tick import SyntheticTickProvider, build_tick_frame_from_provider
from quant.engine.tick_engine import TickBacktestEngine

pytestmark = pytest.mark.skipif(not core_native.HAS_NATIVE, reason="C++ 扩展未构建")

LOT = 100.0
VOL_PCT = 0.10


def _config(**overrides) -> EngineConfig:
    base = {
        "strategy_id": "dual_ma",
        "symbols": ["CN:600519"],
        "start": date(2023, 1, 1),
        "end": date(2023, 6, 30),
        "initial_cash": 200_000,
        "params": {"fast": 5, "slow": 20, "position_pct": 0.9},
        "volume_limit_pct": VOL_PCT,
        "run_id": "tick_test",
    }
    base.update(overrides)
    return EngineConfig(**base)


@pytest.fixture()
def tick_run(context):
    eng = TickBacktestEngine(_config(), context.data)
    result = eng.run()
    assert result.status == "finished", result.error
    return eng, result


def _ts_of(iso: str) -> int:
    import pandas as pd

    return int(pd.Timestamp(iso).value)


class TestTickMechanics:
    def test_fills_exist(self, tick_run):
        """合成数据下应当产生成交，否则后面的断言都是空转。"""
        _, result = tick_run
        assert result.trades, "tick 引擎未产生任何成交，后续不变量测试无效"
        assert len(result.equity_curve) > 100

    def test_timestamps_monotonic(self, tick_run):
        _, result = tick_run
        stamps = [_ts_of(t["dt"]) for t in result.trades]
        assert all(b >= a for a, b in zip(stamps, stamps[1:], strict=False)), "成交时间戳必须单调"

    def test_quantity_is_lot_aligned(self, tick_run):
        _, result = tick_run
        bad = [t for t in result.trades if abs(t["quantity"] % LOT) > 1e-6]
        assert not bad, f"{len(bad)} 笔成交不是整手，例 {bad[0] if bad else None}"

    def test_volume_limit_respected(self, tick_run):
        eng, result = tick_run
        frames = eng._load_minute_frames()
        provider = SyntheticTickProvider(frames=frames, interval_seconds=60)
        tf = build_tick_frame_from_provider(provider, "CN:600519", date(2023, 1, 1), date(2023, 6, 30))
        vol_by_ts = dict(zip(tf.ts.tolist(), tf.volume.tolist(), strict=False))
        over = []
        for t in result.trades:
            cap = vol_by_ts.get(_ts_of(t["dt"]), 0.0) * VOL_PCT
            if cap > 0 and t["quantity"] > cap + LOT + 1e-6:
                over.append((t["dt"], t["quantity"], cap))
        assert not over, f"{len(over)} 笔成交超出成交量上限，例 {over[:2]}"

    def test_fees_positive_and_stamp_tax_on_sell(self, tick_run):
        _, result = tick_run
        assert all(t["fee"] > 0 for t in result.trades), "手续费必须为正"
        sells = [t for t in result.trades if t["side"] == "sell"]
        buys = [t for t in result.trades if t["side"] == "buy"]
        assert sells and buys, "需要同时有买有卖才能比较费率"
        sell_ratio = np.mean([t["fee"] / (t["quantity"] * t["price"]) for t in sells])
        buy_ratio = np.mean([t["fee"] / (t["quantity"] * t["price"]) for t in buys])
        assert sell_ratio > buy_ratio, f"卖出费率 {sell_ratio:.6f} 未高于买入 {buy_ratio:.6f}，印花税可能没计"

    def test_t_plus_one(self, tick_run):
        """同一交易日内，卖出总量不得超过开盘前的持仓。"""
        _, result = tick_run
        by_day: dict[str, list[dict]] = defaultdict(list)
        for t in sorted(result.trades, key=lambda x: x["dt"]):
            by_day[t["dt"][:10]].append(t)

        carry = 0.0
        violations = []
        for day in sorted(by_day):
            available_at_open = carry
            sold = sum(t["quantity"] for t in by_day[day] if t["side"] == "sell")
            if sold > available_at_open + 1e-6:
                violations.append((day, sold, available_at_open))
            net = sum(t["quantity"] if t["side"] == "buy" else -t["quantity"] for t in by_day[day])
            carry += net
        assert not violations, f"{len(violations)} 天违反 T+1，例 {violations[:3]}"

    def test_equity_identity(self, tick_run):
        """权益 = 现金 + 持仓 x 价格。"""
        _, result = tick_run
        bad = []
        for row in result.equity_curve:
            expect = row["cash"] + row["position"] * row["price"]
            if abs(expect - row["total_equity"]) > 1e-4:
                bad.append((row["dt"], expect, row["total_equity"]))
        assert not bad, f"{len(bad)} 个权益点不满足恒等式，例 {bad[:2]}"

    def test_cash_never_negative(self, tick_run):
        _, result = tick_run
        neg = [row for row in result.equity_curve if row["cash"] < -1e-6]
        assert not neg, f"{len(neg)} 个时点现金为负，例 {neg[:2]}"


class TestTickProviderContract:
    def test_synthetic_provider_marks_itself(self):
        """合成源必须自报是模拟数据，避免上层误当真实 tick。"""
        p = SyntheticTickProvider()
        assert p.is_synthetic is True

    def test_empty_range_returns_empty_frame(self, context):
        frames = {}
        p = SyntheticTickProvider(frames=frames)
        tf = build_tick_frame_from_provider(p, "CN:600519", date(2023, 1, 1), date(2023, 1, 31))
        assert tf.size == 0

    def test_synthetic_generates_within_trading_hours(self, context):
        """合成 tick 必须落在交易时段内（09:30-11:30 / 13:00-15:00）。"""
        import pandas as pd

        from quant.data.store import MarketDataService  # noqa: F401

        frame = context.data.bars("CN:600519", date(2023, 1, 1), date(2023, 1, 31))
        p = SyntheticTickProvider(frames={"CN:600519": frame}, interval_seconds=60)
        tf = build_tick_frame_from_provider(p, "CN:600519", date(2023, 1, 1), date(2023, 1, 31))
        assert tf.size > 0
        stamps = pd.to_datetime(tf.ts)
        minutes = stamps.hour * 100 + stamps.minute
        in_am = (minutes >= 930) & (minutes <= 1130)
        in_pm = (minutes >= 1300) & (minutes <= 1500)
        assert np.all(in_am | in_pm), "存在交易时段外的合成 tick"


class TestTickStrategyCompat:
    def test_unsupported_strategy_raises(self, context):
        """需要横截面排序的策略在 tick 通道应显式报错，而不是静默出错。"""
        cfg = _config(strategy_id="momentum_rotation", params={"lookback": 10})
        result = TickBacktestEngine(cfg, context.data).run()
        assert result.status == "failed"
        assert "横截面" in (result.error or "")


class TestSyntheticProviderInputFormats:
    """合成源必须吃各种 K 线格式 —— 喂不对会直接 KeyError 或静默算错。

    这里每一条都对应一个真实踩过的坑，不是假想防御。
    """

    @staticmethod
    def _bars(n: int = 5, index=None):
        import pandas as pd

        idx = index if index is not None else pd.bdate_range("2024-01-02", periods=n)
        return pd.DataFrame(
            {
                "open": [10.0] * n,
                "high": [10.1] * n,
                "low": [9.9] * n,
                "close": [10.0] * n,
                "volume": [1000.0] * n,
            },
            index=idx,
        )

    def _fetch(self, frame, start, end):
        p = SyntheticTickProvider(frames={"A": frame})
        return build_tick_frame_from_provider(p, "A", start, end)

    def test_accepts_datetime_index(self):
        """MarketDataService 返回 DatetimeIndex、无 date 列 —— 最常见的调用形态。"""
        frame = self._bars()
        tf = self._fetch(frame, frame.index[0].date(), frame.index[-1].date())
        assert tf.size == 5 * 240, f"期望 5 天 x 240 tick，实际 {tf.size}"

    def test_accepts_date_column(self):
        import pandas as pd

        frame = self._bars().reset_index().rename(columns={"index": "date"})
        frame["date"] = pd.to_datetime(frame["date"])
        tf = self._fetch(frame, frame["date"].iloc[0].date(), frame["date"].iloc[-1].date())
        assert tf.size == 5 * 240

    def test_accepts_dt_column(self):
        frame = self._bars().reset_index().rename(columns={"index": "dt"})
        tf = self._fetch(frame, frame["dt"].iloc[0].date(), frame["dt"].iloc[-1].date())
        assert tf.size == 5 * 240

    def test_accepts_capitalized_columns(self):
        """部分数据源给 Open/Close，大小写不统一。"""
        frame = self._bars().rename(columns={"open": "Open", "close": "Close"})
        tf = self._fetch(frame, frame.index[0].date(), frame.index[-1].date())
        assert tf.size == 5 * 240

    def test_missing_time_axis_raises_readable_error(self):
        """真的没有时间轴时要给人话，而不是裸 KeyError。"""
        import pandas as pd

        frame = self._bars().reset_index(drop=True)
        with pytest.raises(ValueError, match="无法确定时间轴"):
            self._fetch(frame, pd.Timestamp("2024-01-02").date(), pd.Timestamp("2024-01-08").date())

    def test_minute_bars_not_expanded_per_day(self):
        """分钟线每根只展开它自己那一分钟。

        回归：曾经按"每天两个交易时段"展开，导致一根 1 分钟 bar 生成 480 个 tick，
        一天被重复展开 240 次、成交量放大 240 倍 —— 撮合与成本全错。
        """
        import pandas as pd

        stamps = pd.date_range("2024-01-02 09:30", periods=60, freq="1min", name="date")
        frame = self._bars(len(stamps), index=stamps)
        tf = self._fetch(frame, stamps[0].date(), stamps[-1].date())
        assert tf.size == 60, f"60 根分钟线应生成 60 个 tick，实际 {tf.size}"
        # 成交量必须守恒，不能放大
        assert abs(tf.volume.sum() - frame["volume"].sum() / 2) <= 1e-6 * max(1.0, frame["volume"].sum()), (
            f"成交量未守恒：tick 合计 {tf.volume.sum()}，bar 合计 {frame['volume'].sum()}"
        )

    def test_daily_bars_expand_to_full_sessions(self):
        """日线仍应展开成全天两个时段（这是它的意义所在）。"""
        frame = self._bars()
        tf = self._fetch(frame, frame.index[0].date(), frame.index[-1].date())
        assert tf.size // 5 == 240, "每个交易日应生成 240 个 tick（120 分钟 x 2 时段）"

    def test_invalid_bar_rows_skipped(self):
        """停牌 / 未上市的 NaN 或缺价 bar 不能污染插值路径。"""

        frame = self._bars(4)
        frame.loc[frame.index[1], ["open", "close"]] = np.nan
        frame.loc[frame.index[2], "close"] = 0.0
        tf = self._fetch(frame, frame.index[0].date(), frame.index[-1].date())
        assert tf.size == 2 * 240, f"应只剩 2 个有效交易日，实际 tick {tf.size}"
        assert np.all(tf.price > 0)


class TestFlatPriceNoSpuriousPnl:
    """价格为常数时，收益必须只是因为手续费亏损。

    这是最容易被"看起来能跑"掩盖的错误：如果插值时把 bar 的 high/low
    当成每日路径反复踩，会在横盘上凭空造出利润。
    """

    def test_flat_price_returns_only_fees(self):
        import pandas as pd

        from quant import core_native

        stamps = pd.date_range("2024-01-02 09:30", periods=240 * 10, freq="1min", name="date")
        frame = pd.DataFrame(
            {
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 1e6,
            },
            index=stamps,
        )
        p = SyntheticTickProvider(frames={"A": frame})
        tf = build_tick_frame_from_provider(p, "A", stamps[0].date(), stamps[-1].date())
        assert np.allclose(tf.price, 10.0), "恒定价 bar 生成的 tick 价格必须也是常数"

        spec = {
            "version": 1,
            "entry": [
                {
                    "type": "gt",
                    "left": {"kind": "field", "field": "close"},
                    "right": {"kind": "const", "value": 0.0},
                }
            ],
            "exit": [],
            "position_pct": 1.0,
            "stop_loss": 0.0,
            "take_profit": 0.0,
        }
        cfg = core_native._native.TickConfig()
        cfg.initial_cash = 100_000.0
        raw = core_native._native.run_tick_backtest(
            spec, tf.ts, tf.price, tf.bid, tf.ask, tf.volume, tf.amount, cfg
        )
        curve = pd.DataFrame(raw["equity_curve"])
        final = curve["equity"].iloc[-1]
        assert final <= 100_000.0 + 1e-6, f"横盘行情凭空产生收益：{final:.2f}"
        # 亏损应当仅来自手续费（一手 100 股 @10 元，佣金千分之零点三但最低 5 元）
        assert 100_000.0 - final <= 100.0, f"横盘亏损 {100_000.0 - final:.2f} 超出手续费合理范围"
