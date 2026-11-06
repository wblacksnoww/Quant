"""回归：跑失败的回测必须"看起来失败"。

这三个问题都属于同一类失效——**界面显示一切正常，实际结果全错或永远不结束**：

* 分钟级请求入库时丢了 ``bar_interval``，worker 重建配置时退回 1d，
  15m/60m 静默按日线跑；
* 引擎内部捕获异常后只写了任务队列（backtest_jobs），没有写 runs 表，
  前端轮询 runs 表，于是永远停在 running，错误信息一条也看不到；
* 纸盘分钟回放的默认天数（120）超过数据源 90 天上限，一启动就失败。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from quant.engine.base import EngineConfig, EngineResult
from quant.server.backtest_manager import BacktestManager
from quant.storage.recorder import RunRecorder
from quant.storage.repositories.runs import RunRepository


def _config(**kwargs) -> EngineConfig:
    base = {
        "strategy_id": "dual_ma",
        "symbols": ["CN:000001"],
        "start": date(2026, 1, 1),
        "end": date(2026, 11, 6),
        "initial_cash": 1_000_000.0,
        "run_id": "run_unit_0001",
        "user_id": 1,
    }
    base.update(kwargs)
    return EngineConfig(**base)


# --------------------------------------------------------------------------- #
# 入库配置必须能无损还原
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("interval", ["1d", "1m", "5m", "15m", "30m", "60m"])
def test_config_payload_round_trip_keeps_bar_interval(interval):
    """样例：提交 15m 回测，worker 重建配置后仍然是 15m。"""
    original = _config(bar_interval=interval, direction="short")
    payload = BacktestManager._config_payload(original)
    assert payload["bar_interval"] == interval, "入库 payload 丢了 bar_interval，分钟级会退化成日线"
    assert payload["direction"] == "short"

    restored = BacktestManager._config_from_payload(payload, run_id=original.run_id, user_id=1)
    assert restored.bar_interval == original.bar_interval
    assert restored.direction == original.direction
    assert restored.symbols == original.symbols


def test_config_payload_covers_every_field_that_survives_restore():
    """payload 与重建函数的字段必须对得上，不能只补一个 bar_interval。"""
    config = _config(bar_interval="15m", benchmark="CN:000300", adjust="qfq", volume_limit_pct=0.2)
    payload = BacktestManager._config_payload(config)
    restored = BacktestManager._config_from_payload(payload, run_id=config.run_id, user_id=1)

    for field in (
        "strategy_id",
        "symbols",
        "start",
        "end",
        "initial_cash",
        "benchmark",
        "adjust",
        "volume_limit_pct",
        "risk_free_rate",
        "bar_interval",
        "direction",
    ):
        assert getattr(restored, field) == getattr(config, field), f"{field} 在入库/还原过程中变了"


# --------------------------------------------------------------------------- #
# 引擎异常必须在 runs 表里留下 failed
# --------------------------------------------------------------------------- #


def test_engine_failure_is_visible_in_runs_table(context):
    """策略不存在时引擎会抛错，runs 表必须变成 failed 而不是停在 running。"""
    from quant.engine.backtest import BacktestEngine

    config = _config(strategy_id="no_such_strategy", run_id="run_fail_0001")
    recorder = RunRecorder(
        context.db,
        config.run_id,
        user_id=1,
        kind="backtest",
        strategy_id=config.strategy_id,
        symbols=config.symbols,
        config=BacktestManager._config_payload(config),
    )
    recorder.start()

    result = BacktestEngine(config, context.data, recorder=recorder).run()
    assert result.status == "failed", "引擎应当把异常收进结果"
    assert result.error

    with context.db.session() as session:
        record = RunRepository(session).get(config.run_id)
    assert record is not None
    assert record.status == "failed", (
        "引擎抛异常后 runs 表仍停在 running —— 前端会一直转圈，用户看不到任何错误"
    )
    assert record.error
    assert record.progress == pytest.approx(1.0)


def test_engine_failure_finalizes_without_recorder_error(context):
    """没有 recorder 时失败路径同样不能抛二次异常。"""
    from quant.engine.backtest import BacktestEngine

    config = _config(strategy_id="no_such_strategy", run_id="run_fail_0002")
    result = BacktestEngine(config, context.data).run()
    assert result.status == "failed"
    assert isinstance(result, EngineResult)


# --------------------------------------------------------------------------- #
# 纸盘分钟回放不能超过数据源上限
# --------------------------------------------------------------------------- #


class _RecordingData:
    """记录 minute_bars 请求区间的最小数据服务替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[datetime, datetime, str]] = []

    def minute_bars(self, symbol, start, end, freq="1m"):
        self.calls.append((start, end, freq))
        import pandas as pd

        return pd.DataFrame(
            {
                "date": pd.to_datetime([start, start + timedelta(minutes=1)]),
                "open": [1.0, 1.0],
                "high": [1.0, 1.0],
                "low": [1.0, 1.0],
                "close": [1.0, 1.0],
                "volume": [1.0, 1.0],
            }
        )

    def bars(self, *args, **kwargs):  # pragma: no cover - 分钟路径不该走到这里
        raise AssertionError("分钟级纸盘不应退回日线取数")


def _paper_engine(data, *, replay_days: int, start: date, end: date, interval="15m"):
    from quant.engine.paper import PaperEngine, PaperOptions

    config = EngineConfig(
        strategy_id="dual_ma",
        symbols=["CRYPTO:BTC/USDT"],
        start=start,
        end=end,
        bar_interval=interval,
        run_id="run_paper_unit",
    )
    return PaperEngine(
        config,
        data,  # type: ignore[arg-type]
        options=PaperOptions(mode="replay", replay_days=replay_days),
    )


def test_paper_minute_replay_stays_within_source_limit():
    """默认 replay_days=120 会让分钟级纸盘一启动就报"最多 90 天"。"""
    data = _RecordingData()
    engine = _paper_engine(
        data, replay_days=120, start=date(2026, 1, 1), end=date(2026, 11, 6), interval="15m"
    )
    engine._load_frames()

    assert data.calls, "没有发起分钟线请求"
    start, end, freq = data.calls[0]
    span = end - start
    assert freq == "15m"
    assert span <= timedelta(days=90), f"请求了 {span.days} 天，超过数据源 90 天上限"


def test_paper_minute_replay_respects_explicit_start():
    """用户显式指定的 start 比默认回放窗口更近时，以用户的为准。"""
    data = _RecordingData()
    engine = _paper_engine(
        data, replay_days=120, start=date(2026, 11, 1), end=date(2026, 11, 6), interval="60m"
    )
    engine._load_frames()

    start, _end, _freq = data.calls[0]
    assert start.date() >= date(2026, 10, 31), "用户指定了 11-01 起，却按 120 天窗口去拉"


def test_paper_daily_replay_untouched():
    """日线回放不受分钟线上限影响。"""

    class _DailyData(_RecordingData):
        def bars(self, symbol, start, end):  # noqa: D102
            import pandas as pd

            days = pd.date_range(start, end, freq="D")
            return pd.DataFrame(
                {
                    "date": days,
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1.0,
                }
            )

    data = _DailyData()
    engine = _paper_engine(data, replay_days=30, start=date(2026, 1, 1), end=date(2026, 11, 6), interval="1d")
    frames = engine._load_frames()
    assert frames, "日线回放没有取到数据"
    assert not data.calls, "日线回放不该请求分钟线"
