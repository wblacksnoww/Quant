"""参数寻优与 walk-forward。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from quant.optimize import (
    MAX_COMBINATIONS,
    OptimizeCancelled,
    Optimizer,
    OptimizeRequest,
    SpaceError,
    WalkForwardError,
    expand_space,
    make_folds,
    sample_space,
)

# --------------------------------------------------------------------------- #
# 参数空间
# --------------------------------------------------------------------------- #


def test_expand_enumerated_values():
    combos = expand_space({"fast": [5, 10], "slow": [20, 30]})
    assert len(combos) == 4
    assert {"fast": 5, "slow": 20} in combos
    assert {"fast": 10, "slow": 30} in combos


def test_expand_integer_range_is_inclusive_of_stop():
    combos = expand_space({"fast": {"start": 5, "stop": 15, "step": 5}})
    assert [c["fast"] for c in combos] == [5, 10, 15]


def test_expand_float_range_has_no_binary_noise():
    """浮点步进不能出现 0.030000000000000002 这种值。

    这些参数会直接进 spec 参与 C++ 求值，脏值会让"0.03 与 0.03000000000000000x"
    被当成两个不同参数，白白多跑一倍组合。
    """
    combos = expand_space({"stop_loss": {"start": 0.02, "stop": 0.06, "step": 0.01}})
    values = [c["stop_loss"] for c in combos]
    assert values == [0.02, 0.03, 0.04, 0.05, 0.06]
    assert all(repr(v).count("0") < 6 for v in values)


def test_scalar_becomes_single_value_axis():
    combos = expand_space({"position_pct": 0.95})
    assert combos == [{"position_pct": 0.95}]


def test_combination_limit_is_enforced_not_truncated():
    """超过上限必须报错。

    截断的话用户以为搜了整个空间，实际只搜了前 2000 组 —— 而"最优"
    往往就藏在没跑的那部分里，结论完全错误。
    """
    space = {"a": list(range(60)), "b": list(range(60))}
    with pytest.raises(SpaceError) as exc:
        expand_space(space)
    assert str(MAX_COMBINATIONS) in str(exc.value)


def test_empty_space_rejected():
    with pytest.raises(SpaceError):
        expand_space({})


def test_nonpositive_step_rejected():
    with pytest.raises(SpaceError):
        expand_space({"fast": {"start": 1, "stop": 10, "step": 0}})


def test_random_sample_is_deterministic_with_seed():
    space = {"fast": {"start": 1, "stop": 20, "step": 1}}
    a = sample_space(space, 5, seed=42)
    b = sample_space(space, 5, seed=42)
    assert a == b
    assert len(a) == 5
    assert len({tuple(sorted(x.items())) for x in a}) == 5  # 不重复


def test_random_sample_caps_at_space_size():
    combos = sample_space({"fast": [1, 2]}, 100)
    assert len(combos) == 2


# --------------------------------------------------------------------------- #
# Walk-forward 切分
# --------------------------------------------------------------------------- #


def test_folds_are_contiguous_and_non_overlapping():
    folds = make_folds(date(2023, 1, 1), date(2024, 6, 30), train_days=270, test_days=90)
    assert folds, "应该切出至少一折"
    for fold in folds:
        assert fold.test_start == fold.train_end + timedelta(days=1)
        assert fold.train_days == 270
        assert fold.test_days == 90
    # 相邻折的测试段首尾相接（默认 step == test_days）
    for prev, cur in zip(folds, folds[1:], strict=False):
        assert cur.test_start == prev.test_end + timedelta(days=1)


def test_fold_never_exceeds_range():
    start, end = date(2023, 1, 1), date(2024, 6, 30)
    for fold in make_folds(start, end, train_days=200, test_days=60):
        assert fold.train_start >= start
        assert fold.test_end <= end


def test_overlapping_test_windows_rejected():
    """步长小于测试段会让样本外收益被重复计入。"""
    with pytest.raises(WalkForwardError) as exc:
        make_folds(date(2023, 1, 1), date(2024, 12, 31), train_days=100, test_days=90, step_days=30)
    assert "重叠" in str(exc.value)


def test_range_too_short_for_one_fold():
    with pytest.raises(WalkForwardError):
        make_folds(date(2024, 1, 1), date(2024, 1, 20), train_days=100, test_days=30)


def test_larger_step_leaves_gaps_but_is_allowed():
    folds = make_folds(date(2023, 1, 1), date(2024, 12, 31), train_days=200, test_days=60, step_days=90)
    assert len(folds) >= 2
    for prev, cur in zip(folds, folds[1:], strict=False):
        assert cur.test_start > prev.test_end


# --------------------------------------------------------------------------- #
# 执行器
# --------------------------------------------------------------------------- #


def _request(**overrides) -> OptimizeRequest:
    base = {
        "strategy_id": "dual_ma",
        "symbols": ["CN:600519"],
        "start": date(2023, 1, 1),
        "end": date(2024, 6, 30),
        "space": {"fast": [5, 10], "slow": [20, 30]},
        "objective": "sharpe",
        "initial_cash": 1_000_000,
        "min_trades": 1,
    }
    base.update(overrides)
    return OptimizeRequest(**base)


def test_search_ranks_all_combinations(context):
    result = Optimizer(context.data).search(_request())
    assert result.mode == "search"
    assert len(result.trials) == 4
    assert result.summary["ok"] + result.summary["skipped"] + result.summary["failed"] == 4
    assert result.best is not None
    # 最优值必须真的是所有有效 trial 里的最大
    ok = [t.score for t in result.trials if t.status == "ok" and t.score is not None]
    assert result.best.score == max(ok)


def test_search_marks_low_trade_combinations_as_skipped(context):
    """成交次数不足的组合不能参与排名。

    没有这道门槛，寻优几乎总会选出"只交易一次侥幸赚钱"的参数。
    """
    result = Optimizer(context.data).search(_request(min_trades=10_000))
    assert all(t.status == "skipped" for t in result.trials if t.status != "failed")
    assert result.best is None
    assert "没有任何参数组合" in result.summary.get("note", "")


def test_search_survives_bad_parameter_combination(context, monkeypatch):
    """某组参数跑崩不能中断整个寻优，只标记该组失败。

    真实场景里是 C++ 侧对某些 spec 抛异常、或某区间数据不足。寻优跑几十组，
    因为一组崩掉让整轮白跑是不可接受的 —— 但也不能把失败静默当成功。
    """
    from quant.engine import BacktestEngine

    real_run = BacktestEngine.run
    state = {"n": 0}

    def flaky_run(self):
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("模拟某组参数崩溃")
        return real_run(self)

    monkeypatch.setattr(BacktestEngine, "run", flaky_run)
    result = Optimizer(context.data).search(_request(space={"fast": [5, 10], "slow": [20]}, min_trades=0))

    assert len(result.trials) == 2
    assert any(t.status == "ok" for t in result.trials)
    failed = [t for t in result.trials if t.status == "failed"]
    assert len(failed) == 1
    assert "模拟某组参数崩溃" in (failed[0].error or "")
    assert result.best is not None


def test_unknown_objective_rejected(context):
    with pytest.raises(ValueError, match="未知的寻优目标"):
        Optimizer(context.data).search(_request(objective="magic"))


def test_cancellation_stops_early(context):
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    optimizer = Optimizer(context.data, cancel=cancel)
    with pytest.raises(OptimizeCancelled):
        optimizer.search(_request(space={"fast": list(range(1, 40))}))


def test_walk_forward_produces_oos_metrics(context):
    result = Optimizer(context.data).walk_forward(
        _request(train_days=270, test_days=90, space={"fast": [5, 10], "slow": [20]})
    )
    assert result.mode == "walk_forward"
    assert result.folds
    with_oos = [f for f in result.folds if f.test_metrics]
    assert with_oos, "至少一折应产出样本外结果"
    assert result.summary["folds_with_oos"] == len(with_oos)
    # WFE 要么是有限数，要么明确为 None（样本内年化为 0 时无法计算）
    assert "wfe" in result.summary


def test_walk_forward_reports_unstable_params(context):
    """不同折选出不同参数时必须标出不稳定。

    这是过拟合最直观的信号：参数每折都在跳，说明它只是在拟合各自的训练段。
    """
    result = Optimizer(context.data).walk_forward(
        _request(
            train_days=270,
            test_days=90,
            space={"fast": {"start": 2, "stop": 20, "step": 2}, "slow": [30, 60]},
        )
    )
    assert "params_stable" in result.summary
    assert isinstance(result.summary["params_stable"], bool)


# --------------------------------------------------------------------------- #
# HTTP 接口
# --------------------------------------------------------------------------- #


def _api_payload(**overrides) -> dict:
    payload = {
        "strategy_id": "dual_ma",
        "symbols": ["CN:600519"],
        "start": "2023-01-01",
        "end": "2024-06-30",
        "space": {"fast": [5, 10], "slow": [20]},
        "objective": "sharpe",
        "initial_cash": 1_000_000,
        "min_trades": 0,
    }
    payload.update(overrides)
    return payload


def _wait_job(client, headers, job_id: str, timeout: float = 30.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    detail: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/optimize/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        detail = response.json()
        if detail["status"] in {"finished", "failed", "cancelled"}:
            return detail
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内未结束：{detail.get('message')}")


def test_api_search_roundtrip(client):
    from conftest import login_headers

    headers = login_headers(client)
    response = client.post("/api/optimize", json=_api_payload(), headers=headers)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert response.json()["mode"] == "search"

    detail = _wait_job(client, headers, job_id)
    assert detail["status"] == "finished", detail.get("error")
    assert detail["progress"] == 1.0
    assert detail["total_trials"] == 2
    assert detail["best"] is not None
    assert "summary" in detail

    listing = client.get("/api/optimize", headers=headers).json()
    assert any(item["job_id"] == job_id for item in listing)


def test_api_walk_forward_roundtrip(client):
    from conftest import login_headers

    headers = login_headers(client)
    response = client.post(
        "/api/optimize",
        json=_api_payload(
            walk_forward=True,
            walk_forward_options={"train_days": 270, "test_days": 90},
            space={"fast": [5, 10], "slow": [20]},
        ),
        headers=headers,
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert response.json()["mode"] == "walk_forward"

    detail = _wait_job(client, headers, job_id)
    assert detail["status"] == "finished", detail.get("error")
    assert detail["folds"], "walk-forward 应返回各折结果"


def test_api_rejects_oversized_space(client):
    from conftest import login_headers

    headers = login_headers(client)
    space = {"a": list(range(60)), "b": list(range(60))}
    response = client.post("/api/optimize", json=_api_payload(space=space), headers=headers)
    assert response.status_code == 422, response.text


def test_api_rejects_unknown_objective(client):
    from conftest import login_headers

    headers = login_headers(client)
    response = client.post("/api/optimize", json=_api_payload(objective="magic"), headers=headers)
    assert response.status_code == 422, response.text


def test_api_rejects_mixed_markets(client):
    """混市场标的必须在入口被拦下，而不是跑完才发现结果无意义。"""
    from conftest import login_headers

    headers = login_headers(client)
    response = client.post(
        "/api/optimize",
        json=_api_payload(symbols=["CN:600519", "US:AAPL"]),
        headers=headers,
    )
    assert response.status_code == 422, response.text
