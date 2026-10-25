"""Python 算子（研究通道）测试。

覆盖三件事：
1. 算子注册、解析、计算本身的正确性
2. 算子作为字段进入 spec 后，C++ 主循环真的用上了它
3. 因果性检查能抓出"看了未来"的算子
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant import core_native
from quant.engine import BacktestEngine, EngineConfig
from quant.strategy.operators import (
    assert_causal,
    compute_operator,
    is_operator_field,
    operator_fields_in_spec,
    parse_operator_field,
    register_operator,
    registered,
)

SYMBOL = "CN:600519"


def _frame(n: int = 200, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    idx = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame(
        {
            "date": idx,
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1e5, 5e5, n).astype(float),
        }
    )


class TestOperatorRegistry:
    def test_builtin_operators_registered(self):
        names = set(registered())
        assert {"zscore", "vol_ratio", "gap"} <= names, f"内置算子缺失，实际 {sorted(names)}"

    def test_field_format_parsing(self):
        name, params = parse_operator_field("op:zscore(period=20)")
        assert name == "zscore" and params == {"period": 20.0}

    def test_field_without_params(self):
        name, params = parse_operator_field("op:gap()")
        assert name == "gap" and params == {}

    def test_bad_field_format_raises(self):
        for bad in ("op:zscore", "op:zscore(period)", "op:zscore(period=abc)", "zscore(period=1)"):
            with pytest.raises(ValueError):
                parse_operator_field(bad)

    def test_is_operator_field(self):
        assert is_operator_field("op:zscore(period=5)")
        assert not is_operator_field("close")
        assert not is_operator_field("news_sentiment")

    def test_duplicate_registration_rejected(self):
        with pytest.raises(ValueError, match="已注册"):
            register_operator("zscore")(lambda frame, period=20: frame["close"])


class TestOperatorComputation:
    def test_zscore_statistics(self):
        frame = _frame()
        values = compute_operator(frame, "op:zscore(period=20)")
        assert len(values) == len(frame)
        # warmup 段必须是 NaN，不能是 0（0 会被 C++ 当成合法信号）
        assert np.isnan(values[:19]).all(), "zscore warmup 段应为 NaN"
        assert np.isfinite(values[19:]).all()
        # 滚动窗口内 z-score 的均值应接近 0
        tail = values[25:]
        assert abs(float(np.nanmean(tail))) < 0.6, f"zscore 均值偏离 0 过多：{np.nanmean(tail)}"

    def test_gap_matches_manual(self):
        frame = _frame()
        values = compute_operator(frame, "op:gap()")
        manual = (frame["open"] - frame["close"].shift(1)) / frame["close"].shift(1)
        assert np.allclose(values[1:], manual.to_numpy()[1:], equal_nan=True)

    def test_unregistered_operator_returns_all_nan(self):
        """未注册算子不抛异常（否则整个回测 failed），而是全 NaN + 告警。"""
        values = compute_operator(_frame(), "op:does_not_exist(period=5)")
        assert len(values) == 200
        assert np.isnan(values).all()

    def test_operator_raising_returns_nan_not_crash(self):
        @register_operator("_test_boom", x=("int", 1))
        def _boom(frame, x=1):
            raise RuntimeError("故意炸")

        values = compute_operator(_frame(), "op:_test_boom(x=1)")
        assert np.isnan(values).all()

    def test_wrong_length_returns_nan(self):
        @register_operator("_test_short", )
        def _short(frame):
            return pd.Series([1.0, 2.0])

        values = compute_operator(_frame(), "op:_test_short()")
        assert np.isnan(values).all()


class TestCausalityCheck:
    def test_causal_operator_passes(self):
        ok, reason = assert_causal(_frame(), "op:zscore(period=20)")
        assert ok, reason

    def test_lookahead_operator_detected(self):
        """用了 shift(-1) 的算子必须被抓住。"""

        @register_operator("_test_peek")
        def _peek(frame):
            return frame["close"].shift(-1)

        ok, reason = assert_causal(_frame(), "op:_test_peek()")
        assert not ok, "shift(-1) 的算子未被识别为未来函数"
        assert "未来" in reason

    def test_full_sample_normalization_detected(self):
        """全样本标准化也是未来函数（均值用了全期数据）。"""

        @register_operator("_test_fullnorm")
        def _fullnorm(frame):
            close = frame["close"]
            return (close - close.mean()) / close.std()

        ok, reason = assert_causal(_frame(), "op:_test_fullnorm()")
        assert not ok, "全样本标准化未被识别为未来函数"


class TestOperatorFieldInSpec:
    def test_operator_fields_extracted_from_v1(self):
        spec = {
            "entry": [
                {
                    "type": "lt",
                    "left": {"kind": "field", "field": "op:zscore(period=20)"},
                    "right": {"kind": "const", "value": -1.5},
                }
            ],
            "exit": [],
        }
        assert operator_fields_in_spec(spec) == ["op:zscore(period=20)"]

    def test_no_operators_returns_empty(self):
        spec = {
            "entry": [
                {
                    "type": "gt",
                    "left": {"kind": "field", "field": "close"},
                    "right": {"kind": "const", "value": 0.0},
                }
            ],
            "exit": [],
        }
        assert operator_fields_in_spec(spec) == []


@pytest.mark.skipif(not core_native.HAS_NATIVE, reason="C++ 扩展未构建")
class TestOperatorRunsThroughCpp:
    """算子作为 extras 列喂给 C++ 后，主循环必须真的用上它。"""

    @staticmethod
    def _run(context, spec: dict, run_id: str):
        cfg = EngineConfig(
            strategy_id="custom_rule",
            symbols=[SYMBOL],
            start=date(2023, 1, 1),
            end=date(2024, 6, 28),
            initial_cash=1_000_000,
            params={"spec": spec},
            run_id=run_id,
        )
        return BacktestEngine(cfg, context.data).run()

    @staticmethod
    def _spec(field: str, threshold: float) -> dict:
        return {
            "version": 1,
            "entry": [
                {
                    "type": "lt",
                    "left": {"kind": "field", "field": field},
                    "right": {"kind": "const", "value": threshold},
                }
            ],
            "exit": [
                {
                    "type": "gt",
                    "left": {"kind": "field", "field": field},
                    "right": {"kind": "const", "value": 0.0},
                }
            ],
            "position_pct": 0.9,
            "stop_loss": 0.0,
            "take_profit": 0.0,
        }

    def test_spec_with_operator_validates(self, context):
        result = self._run(context, self._spec("op:zscore(period=20)", -0.5), "op_valid")
        assert result.status == "finished", result.error

    def test_operator_really_drives_trades(self, context):
        """门槛放宽应产生成交 —— 证明列不是全 NaN。"""
        loose = self._run(context, self._spec("op:zscore(period=20)", 0.5), "op_loose")
        assert loose.status == "finished", loose.error
        assert loose.trades, (
            "算子字段 spec 零成交。若列是全 NaN，说明算子没被喂进 C++。"
        )

    def test_operator_threshold_changes_result(self, context):
        """不同门槛必须给出不同成交 —— 否则条件没生效。"""
        a = self._run(context, self._spec("op:zscore(period=20)", -0.5), "op_t1")
        b = self._run(context, self._spec("op:zscore(period=20)", 0.5), "op_t2")
        assert a.status == b.status == "finished"
        ta = [(t["dt"], t["side"]) for t in a.trades]
        tb = [(t["dt"], t["side"]) for t in b.trades]
        assert ta != tb, "不同门槛产生了相同成交序列，算子条件未生效"

    def test_unregistered_operator_yields_no_trades_and_warns(self, context):
        result = self._run(
            context, self._spec("op:no_such_op(period=5)", 0.5), "op_missing"
        )
        assert result.status == "finished", result.error
        assert result.trades == [], "未注册算子的列是全 NaN，条件恒不成立，不该有成交"
        msgs = " ".join(str(item.get("message", "")) for item in (result.logs or []))
        # 必须点名是哪个算子、且说明为什么没用 —— 只说"算子"两个字测试会假通过
        assert "no_such_op" in msgs, f"告警未点名具体算子，logs={result.logs}"
        assert "未注册" in msgs, f"告警未说明未注册，logs={result.logs}"
        assert any(
            str(item.get("level")) == "error" for item in (result.logs or [])
        ), f"不可用算子应以 error 级别呈现，logs={result.logs}"

    def test_bad_param_operator_rejected_at_validation(self, context):
        """参数格式错误在校验阶段就拦住，并指明位置 —— 比运行时告警更有用。"""
        spec = {
            "version": 1,
            "entry": [
                {
                    "type": "lt",
                    "left": {"kind": "field", "field": "op:zscore(period=abc)"},
                    "right": {"kind": "const", "value": 0.5},
                }
            ],
            "exit": [
                {
                    "type": "gt",
                    "left": {"kind": "field", "field": "close"},
                    "right": {"kind": "const", "value": 1e9},
                }
            ],
            "position_pct": 0.9,
            "stop_loss": 0.0,
            "take_profit": 0.0,
        }
        result = self._run(context, spec, "op_badparam")
        assert result.status == "failed", "参数格式错误应在校验阶段失败"
        err = result.error or ""
        assert "period" in err and "数字" in err, f"错误信息未指明参数问题：{err}"
        assert "entry[0].left.field" in err, f"错误信息未指明位置：{err}"

    def test_multiple_operators_mixed_ok_and_bad(self, context):
        """一个可用 + 一个不可用时，可用那个仍需正常工作。"""
        spec = {
            "version": 1,
            "entry": [
                {
                    "type": "lt",
                    "left": {"kind": "field", "field": "op:zscore(period=20)"},
                    "right": {"kind": "const", "value": 0.5},
                },
                {
                    "type": "gt",
                    "left": {"kind": "field", "field": "op:ghost()"},
                    "right": {"kind": "const", "value": -1e9},
                },
            ],
            "exit": [
                {
                    "type": "gt",
                    "left": {"kind": "field", "field": "op:zscore(period=20)"},
                    "right": {"kind": "const", "value": 0.0},
                }
            ],
            "position_pct": 0.9,
            "stop_loss": 0.0,
            "take_profit": 0.0,
        }
        result = self._run(context, spec, "op_mixed")
        assert result.status == "finished", result.error
        msgs = " ".join(str(item.get("message", "")) for item in (result.logs or []))
        assert "ghost" in msgs, "不可用算子未被点名"
        assert "zscore(period=20)" in msgs, "可用算子应被记录为已启用"

    def test_operator_run_is_labeled_as_research_channel(self, context):
        """算子跑出结果必须在日志里标明是研究通道，避免与 C++ 结果混为一谈。"""
        result = self._run(context, self._spec("op:zscore(period=20)", 0.5), "op_label")
        msgs = " ".join(str(item.get("message", "")) for item in (result.logs or []))
        assert "Python 算子" in msgs or "研究通道" in msgs, (
            f"算子回测未标注研究通道，logs={result.logs}"
        )
