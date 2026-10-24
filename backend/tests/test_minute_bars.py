"""分钟级回测/模拟盘：数据拉取缓存、引擎 bar_interval 与端到端回测。"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from quant.engine import BacktestEngine, EngineConfig


@pytest.fixture()
def fake_minute_provider(monkeypatch, context):
    """把 crypto provider 的分钟历史替换成确定性合成数据（不联网）。"""
    from quant.data.providers.synthetic import SyntheticProvider

    synth = SyntheticProvider()

    def fake_intraday_range(self, symbol, start, end, *, freq="1m"):
        # 以日线为骨架合成 1m bars：每天 3 根（00:00 / 08:00 / 16:00 UTC）
        daily = synth.daily(symbol, start.date(), end.date())
        rows = []
        for _, row in daily.iterrows():
            day = pd.Timestamp(row["date"])
            for hour, drift in ((0, 0.99), (8, 1.0), (16, 1.01)):
                close = float(row["close"]) * drift
                rows.append(
                    {
                        "date": day + pd.Timedelta(hours=hour),
                        "open": close * 0.999,
                        "high": close * 1.001,
                        "low": close * 0.998,
                        "close": close,
                        "volume": 1000.0,
                        "amount": close * 1000.0,
                    }
                )
        frame = pd.DataFrame(rows)
        frame = frame[(frame["date"] >= pd.Timestamp(start)) & (frame["date"] < pd.Timestamp(end))]
        return frame.reset_index(drop=True)

    monkeypatch.setattr(
        "quant.data.providers.crypto_provider.CryptoProvider.intraday_range",
        fake_intraday_range,
    )
    return synth


def test_minute_bars_cached(context, fake_minute_provider):
    end = datetime(2024, 6, 28, 23, 59)
    start = datetime(2024, 6, 20)
    first = context.data.minute_bars("CRYPTO:BTC/USDT", start, end, freq="1m")
    assert len(first) > 0
    assert list(first.columns) >= ["date", "open", "high", "low", "close", "volume"]
    # 第二次走缓存
    second = context.data.minute_bars("CRYPTO:BTC/USDT", start, end, freq="1m")
    pd.testing.assert_frame_equal(first, second)


def test_minute_backtest_end_to_end(context, fake_minute_provider):
    config = EngineConfig(
        strategy_id="buy_hold",
        symbols=["CRYPTO:BTC/USDT"],
        start=date(2024, 6, 20),
        end=date(2024, 6, 28),
        initial_cash=1_000_000,
        bar_interval="1m",
        run_id="run_minute_bt",
    )
    result = BacktestEngine(config, context.data).run()
    assert result.status == "finished"
    assert result.metrics.final_equity > 0
    # 分钟级 equity 曲线点数应远多于日级
    # 合成数据每天 3 根 1m bar，7 个交易日 ≈ 21 步；日级只有 7 步
    assert len(result.equity_curve) > 10


def test_minute_interval_rejected_for_non_crypto(context):
    config = EngineConfig(
        strategy_id="buy_hold",
        symbols=["CN:600519"],
        start=date(2024, 1, 1),
        end=date(2024, 6, 28),
        bar_interval="1m",
        run_id="run_bad_interval",
    )
    with pytest.raises(ValueError):
        config.validate()


def test_paper_minute_replay_runs(context, fake_minute_provider):
    """纸盘分钟回放端到端（直接驱动引擎，不起线程）。"""
    from quant.engine.paper import PaperEngine, PaperOptions

    config = EngineConfig(
        strategy_id="buy_hold",
        symbols=["CRYPTO:BTC/USDT"],
        start=date(2024, 6, 20),
        end=date(2024, 6, 28),
        initial_cash=100_000,
        bar_interval="5m",
        run_id="run_minute_paper",
    )
    engine = PaperEngine(
        config,
        context.data,
        options=PaperOptions(mode="replay", replay_days=10, replay_interval=0.0),
    )
    result = engine.run()
    assert result.status == "finished"
    assert len(result.equity_curve) > 10
