"""条件拼装策略：spec 校验、信号语义、引擎端到端、API 契约与用户隔离。"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from quant.engine import BacktestEngine, EngineConfig
from quant.strategy.custom import STRATEGY_ID, parse_spec, validate_spec

from conftest import login_headers  # pytest 把 tests 目录加入 sys.path

# --------------------------------------------------------------------------- #
# 测试用 spec
# --------------------------------------------------------------------------- #

GOLDEN_CROSS_SPEC = {
    "version": 1,
    "description": "双均线金叉",
    "category": "trend",
    "entry": [
        {
            "type": "cross_above",
            "left": {"kind": "indicator", "name": "sma", "params": {"period": 5}},
            "right": {"kind": "indicator", "name": "sma", "params": {"period": 20}},
        }
    ],
    "exit": [
        {
            "type": "cross_below",
            "left": {"kind": "indicator", "name": "sma", "params": {"period": 5}},
            "right": {"kind": "indicator", "name": "sma", "params": {"period": 20}},
        }
    ],
    "position_pct": 0.9,
    "stop_loss": 0.08,
}

ALWAYS_BUY_SPEC = {
    "entry": [{"type": "gt", "left": {"kind": "field", "field": "close"}, "right": {"kind": "const", "value": 0}}],
    "exit": [{"type": "lt", "left": {"kind": "field", "field": "close"}, "right": {"kind": "const", "value": 1e12}}],
    "position_pct": 0.5,
    "stop_loss": 0.0,
}

RSI_SPEC = {
    "entry": [
        {
            "type": "lt",
            "left": {"kind": "indicator", "name": "rsi", "params": {"period": 14}},
            "right": {"kind": "const", "value": 35},
        }
    ],
    "exit": [
        {
            "type": "gt",
            "left": {"kind": "indicator", "name": "rsi", "params": {"period": 14}},
            "right": {"kind": "const", "value": 65},
        }
    ],
}


# --------------------------------------------------------------------------- #
# spec 解析与校验
# --------------------------------------------------------------------------- #


def test_parse_spec_accepts_and_normalizes():
    spec = parse_spec(GOLDEN_CROSS_SPEC)
    assert len(spec.entry) == 1 and len(spec.exit) == 1
    assert spec.position_pct == 0.9
    assert spec.stop_loss == 0.08
    assert spec.lookback == 20  # 最大指标周期
    assert spec.entry[0].left.name == "sma"

    normalized = validate_spec(GOLDEN_CROSS_SPEC)
    assert normalized["entry"][0]["left"]["params"]["period"] == 5


def test_parse_spec_accepts_json_string():
    spec = parse_spec(json.dumps(GOLDEN_CROSS_SPEC))
    assert spec.lookback == 20


@pytest.mark.parametrize(
    "mutate, keyword",
    [
        (lambda s: s.update({"hacker": 1}), "未知字段"),
        (lambda s: s.update({"entry": []}), "非空"),
        (lambda s: s.update({"exit": []}), "非空"),
        (lambda s: s.update({"version": 99}), "版本"),
        (lambda s: s.update({"category": "whatever"}), "category"),
        (lambda s: s.update({"position_pct": 1.5}), "position_pct"),
        (lambda s: s.update({"stop_loss": 1.2}), "stop_loss"),
        (
            lambda s: s["entry"].__setitem__(
                0, {"type": "gt", "left": {"kind": "indicator", "name": "secret_math", "params": {}}, "right": {"kind": "const", "value": 1}}
            ),
            "只支持",
        ),
        (
            lambda s: s["entry"].__setitem__(
                0, {"type": "eval", "left": {"kind": "const", "value": 1}, "right": {"kind": "const", "value": 1}}
            ),
            "type",
        ),
        (
            lambda s: s["entry"].__setitem__(
                0, {"type": "gt", "left": {"kind": "field", "field": "password_hash"}, "right": {"kind": "const", "value": 1}}
            ),
            "field",
        ),
        (
            lambda s: s["entry"].__setitem__(
                0, {"type": "gt", "left": {"kind": "const", "value": "1+1"}, "right": {"kind": "const", "value": 1}}
            ),
            "value",
        ),
        (
            lambda s: s["entry"].__setitem__(0, {"type": "gt", "right": {"kind": "const", "value": 1}}),
            "缺少 left",
        ),
        (
            lambda s: s["entry"][0]["left"].update({"params": {"period": 1}}),
            "之间",
        ),
        (
            lambda s: s["entry"][0]["left"].update({"params": {"period": 99999}}),
            "之间",
        ),
        (
            lambda s: s["entry"][0]["left"].update({"params": {"period": 5, "evil": 1}}),
            "不支持参数",
        ),
        (
            lambda s: s.update({"entry": [{"type": "gt", "left": {"kind": "const", "value": 1}, "right": {"kind": "const", "value": 1}}] * 7}),
            "最多",
        ),
    ],
)
def test_parse_spec_rejects(mutate, keyword):
    raw = json.loads(json.dumps(GOLDEN_CROSS_SPEC))  # 深拷贝
    mutate(raw)
    with pytest.raises(ValueError, match=keyword):
        parse_spec(raw)


def test_parse_spec_rejects_bad_json_string():
    with pytest.raises(ValueError, match="JSON"):
        parse_spec("{not json")


# --------------------------------------------------------------------------- #
# 信号语义
# --------------------------------------------------------------------------- #


def _frame(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
            "amount": [1000.0] * n,
        }
    )


def _series(spec: dict, frame: pd.DataFrame):
    from quant.strategy.custom import _condition_series

    parsed = parse_spec(spec)
    cache: dict = {}
    return _condition_series(frame, parsed.entry[0], cache)


def test_compare_condition_semantics():
    frame = _frame([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    cond_series = _series(
        {
            "entry": [{"type": "gt", "left": {"kind": "field", "field": "close"}, "right": {"kind": "const", "value": 4}}],
            "exit": [{"type": "gt", "left": {"kind": "field", "field": "close"}, "right": {"kind": "const", "value": 100}}],
        },
        frame,
    )
    assert list(cond_series.astype(int)) == [0, 0, 0, 0, 1, 1, 1]


def test_cross_condition_fires_once_on_trend_change():
    closes = [10.0] * 6 + [c * 1.2 for c in [10, 11, 12, 13, 14, 15]] + [16.0, 15.0, 13.0, 11.0, 9.0, 8.0, 7.0]
    frame = _frame(closes)
    always_true = {"type": "gt", "left": {"kind": "field", "field": "close"}, "right": {"kind": "const", "value": 0}}
    golden = _series(
        {
            "entry": [
                {
                    "type": "cross_above",
                    "left": {"kind": "indicator", "name": "sma", "params": {"period": 3}},
                    "right": {"kind": "indicator", "name": "sma", "params": {"period": 6}},
                }
            ],
            "exit": [always_true],
        },
        frame,
    )
    death = _series(
        {
            "entry": [
                {
                    "type": "cross_below",
                    "left": {"kind": "indicator", "name": "sma", "params": {"period": 3}},
                    "right": {"kind": "indicator", "name": "sma", "params": {"period": 6}},
                }
            ],
            "exit": [always_true],
        },
        frame,
    )
    # 前段数据不足：信号应为 False；上行段出现金叉（idx6）、下行段出现死叉（idx14）
    assert not golden.iloc[:6].any()
    assert golden.iloc[6:].any()
    assert list(death.astype(int)).count(1) == 1
    assert death.iloc[14]


# --------------------------------------------------------------------------- #
# 引擎端到端（合成行情）
# --------------------------------------------------------------------------- #


def custom_config(spec: dict, run_id: str) -> EngineConfig:
    return EngineConfig(
        strategy_id=STRATEGY_ID,
        symbols=["600519"],
        start=date(2023, 1, 1),
        end=date(2024, 6, 30),
        initial_cash=1_000_000,
        params={"spec": spec},
        cost={},
        run_id=run_id,
    )


def test_custom_strategy_runs_and_trades(context):
    result = BacktestEngine(custom_config(ALWAYS_BUY_SPEC, "run_c1"), context.data).run()
    assert result.status == "finished"
    # 买入条件恒真、卖出条件也恒真 → 反复进出，必有成交
    assert result.metrics.trade_count >= 1

    result2 = BacktestEngine(custom_config(ALWAYS_BUY_SPEC, "run_c2"), context.data).run()
    assert result2.metrics.final_equity == pytest.approx(result.metrics.final_equity)


def test_custom_strategy_rejects_bad_spec(context):
    bad = {"entry": [{"type": "nope", "left": {"kind": "const", "value": 1}, "right": {"kind": "const", "value": 1}}]}
    result = BacktestEngine(custom_config(bad, "run_bad"), context.data).run()
    assert result.status == "failed"
    assert "spec 无效" in (result.error or "")


def test_custom_strategy_dual_ma_equivalent(context):
    """金叉/死叉 spec 与内置 dual_ma 在同一数据上应产生相同的交易日序列。"""
    builtin = BacktestEngine(
        EngineConfig(
            strategy_id="dual_ma",
            symbols=["600519"],
            start=date(2023, 1, 1),
            end=date(2024, 6, 30),
            initial_cash=1_000_000,
            params={"fast": 5, "slow": 20, "position_pct": 0.9, "stop_loss": 0.08, "take_profit": 0.0},
            cost={},
            run_id="run_builtin",
        ),
        context.data,
    ).run()
    custom = BacktestEngine(custom_config(GOLDEN_CROSS_SPEC, "run_custom"), context.data).run()

    builtin_days = [t["dt"][:10] for t in builtin.trades if t["side"] == "buy"]
    custom_days = [t["dt"][:10] for t in custom.trades if t["side"] == "buy"]
    assert builtin_days == custom_days


# --------------------------------------------------------------------------- #
# API 契约
# --------------------------------------------------------------------------- #


@pytest.fixture()
def second_user(client, settings):
    """创建一个普通用户并返回其登录头。"""
    admin = login_headers(client)
    resp = client.post(
        "/api/admin/users",
        json={"username": "bob", "password": "Bob!Passw0rd123", "role": "user"},
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    resp = client.post("/api/auth/login", json={"username": "bob", "password": "Bob!Passw0rd123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_custom_strategy_crud_and_isolation(client, second_user):
    mine = login_headers(client)  # admin
    theirs = second_user

    # 创建（admin）
    resp = client.post("/api/strategies/custom", json={"name": "我的金叉", "spec": GOLDEN_CROSS_SPEC}, headers=mine)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["spec"]["entry"][0]["left"]["params"]["period"] == 5
    sid = created["id"]

    # 同名冲突
    resp = client.post("/api/strategies/custom", json={"name": "我的金叉", "spec": GOLDEN_CROSS_SPEC}, headers=mine)
    assert resp.status_code == 409

    # 无效 spec
    bad = {"entry": [{"type": "gt", "left": {"kind": "const", "value": 1}, "right": {"kind": "const", "value": 1}}]}
    resp = client.post("/api/strategies/custom", json={"name": "bad", "spec": {**bad, "exit": bad["entry"], "version": 99}}, headers=mine)
    assert resp.status_code == 422

    # 用户隔离：bob 看不到 admin 的策略
    resp = client.get("/api/strategies/custom", headers=theirs)
    assert resp.status_code == 200 and resp.json() == []
    resp = client.patch(f"/api/strategies/custom/{sid}", json={"name": "偷改"}, headers=theirs)
    assert resp.status_code == 404
    resp = client.delete(f"/api/strategies/custom/{sid}", headers=theirs)
    assert resp.status_code == 404

    # 更新 + 列表
    resp = client.patch(f"/api/strategies/custom/{sid}", json={"description": "改过的描述"}, headers=mine)
    assert resp.status_code == 200 and resp.json()["description"] == "改过的描述"
    resp = client.get("/api/strategies/custom", headers=mine)
    assert [item["id"] for item in resp.json()] == [sid]

    # 删除
    resp = client.delete(f"/api/strategies/custom/{sid}", headers=mine)
    assert resp.status_code == 200
    resp = client.get("/api/strategies/custom", headers=mine)
    assert resp.json() == []


def test_backtest_with_custom_strategy_id(client):
    headers = login_headers(client)
    resp = client.post("/api/strategies/custom", json={"name": "恒买", "spec": ALWAYS_BUY_SPEC}, headers=headers)
    assert resp.status_code == 201, resp.text
    sid = resp.json()["id"]

    # 正常提交
    resp = client.post(
        "/api/backtests",
        json={"strategy_id": "custom_rule", "symbols": ["600519"], "start": "2023-01-01", "end": "2024-06-30", "params": {"custom_strategy_id": sid}},
        headers=headers,
    )
    assert resp.status_code == 202, resp.text

    # 不存在的拼装策略
    resp = client.post(
        "/api/backtests",
        json={"strategy_id": "custom_rule", "symbols": ["600519"], "params": {"custom_strategy_id": 99999}},
        headers=headers,
    )
    assert resp.status_code == 404

    # 内联非法 spec
    resp = client.post(
        "/api/backtests",
        json={
            "strategy_id": "custom_rule",
            "symbols": ["600519"],
            "params": {"spec": {"entry": [], "exit": [{"type": "gt", "left": {"kind": "const", "value": 1}, "right": {"kind": "const", "value": 1}}]}},
        },
        headers=headers,
    )
    assert resp.status_code == 422


def test_backtest_with_disabled_custom_strategy(client):
    headers = login_headers(client)
    resp = client.post("/api/strategies/custom", json={"name": "停用", "spec": ALWAYS_BUY_SPEC}, headers=headers)
    sid = resp.json()["id"]
    resp = client.patch(f"/api/strategies/custom/{sid}", json={"enabled": False}, headers=headers)
    assert resp.status_code == 200
    resp = client.post(
        "/api/backtests",
        json={"strategy_id": "custom_rule", "symbols": ["600519"], "params": {"custom_strategy_id": sid}},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "停用" in resp.json()["detail"]


def test_custom_rule_in_registry():
    from quant.strategy.registry import get_strategy

    schema = get_strategy("custom_rule").schema()
    assert schema["name"] == "条件拼装策略"
    assert any(p["type"] == "json" for p in schema["params"])

# --------------------------------------------------------------------------- #
# spec v2：蓝图节点图
# --------------------------------------------------------------------------- #

from quant.strategy.custom import _condition_series, spec_lookback

from quant.strategy.custom_graph import GraphSpec, graph_lookback, graph_signals

GOLDEN_GRAPH_SPEC = {
    "version": 2,
    "description": "蓝图双均线",
    "category": "trend",
    "position_pct": 0.9,
    "stop_loss": 0.08,
    "nodes": [
        {"id": "s1", "type": "close", "pos": {"x": 0, "y": 0}},
        {"id": "s2", "type": "close", "pos": {"x": 0, "y": 160}},
        {"id": "f1", "type": "sma", "params": {"period": 5}},
        {"id": "f2", "type": "sma", "params": {"period": 20}},
        {"id": "x1", "type": "cross_above"},
        {"id": "x2", "type": "cross_below"},
    ],
    "edges": [
        {"from": "s1", "to": "f1", "to_port": "in"},
        {"from": "s2", "to": "f2", "to_port": "in"},
        {"from": "f1", "to": "x1", "to_port": "left"},
        {"from": "f2", "to": "x1", "to_port": "right"},
        {"from": "f1", "to": "x2", "to_port": "left"},
        {"from": "f2", "to": "x2", "to_port": "right"},
    ],
    "outputs": {"entry": "x1", "exit": "x2"},
}


def graph_variant(mutate):
    import copy

    raw = copy.deepcopy(GOLDEN_GRAPH_SPEC)
    mutate(raw)
    return raw


def test_parse_v2_accepts_and_normalizes():
    # 悬挂节点（编辑器常见遗留物）应保留结构，但不影响预热与信号
    dangling = graph_variant(lambda s: s["nodes"].append({"id": "d1", "type": "close", "pos": {"x": 900, "y": 40}}))
    spec = parse_spec(dangling)
    assert isinstance(spec, GraphSpec)
    assert spec.lookback == 21  # max(5, 20) + 1（交叉自身要前一根）
    assert graph_lookback(spec) == 22  # 与 v1 / dual_ma 的 warmup 对齐

    normalized = validate_spec(dangling)
    assert normalized["version"] == 2
    assert normalized["outputs"] == {"entry": "x1", "exit": "x2"}
    assert any(n["id"] == "d1" for n in normalized["nodes"])  # 布局与节点保留
    s1 = next(n for n in normalized["nodes"] if n["id"] == "s1")
    assert s1["pos"] == {"x": 0.0, "y": 0.0}  # 编辑器坐标透传


@pytest.mark.parametrize(
    "mutate, keyword",
    [
        (lambda s: s.update({"weird": 1}), "未知字段"),
        (lambda s: s["nodes"].__setitem__(0, {"id": "UPPER", "type": "close"}), "id 必须是"),
        (lambda s: s["nodes"].__setitem__(1, {"id": "s1", "type": "close"}), "重复"),
        (lambda s: s["nodes"].__setitem__(0, {"id": "s9", "type": "open_sesame"}), "未知节点类型"),
        (lambda s: s["nodes"].__setitem__(2, {"id": "f1", "type": "sma", "params": {"period": 10.5}}), "整数"),
        (lambda s: s["nodes"].__setitem__(5, {"id": "x2", "type": "cross_below", "params": {"evil": 1}}), "不支持参数"),
        (lambda s: s["nodes"].__setitem__(1, {"id": "s2", "type": "const"}), "value"),
        (lambda s: s["nodes"].__setitem__(0, {"id": "s1", "type": "close", "pos": {"x": "a"}}), "pos"),
        (lambda s: s["edges"].append({"from": "s1", "to": "s1"}), "自环"),
        (lambda s: s["edges"].__setitem__(0, {"from": "s1", "to": "nope"}), "不存在"),
        (lambda s: s["edges"].append({"from": "s1", "to": "f1", "to_port": "in"}), "连接了多次"),
        (lambda s: s["edges"].append({"from": "x1", "to": "x2", "to_port": "left"}), "布尔"),
        (lambda s: s["edges"].__setitem__(1, {"from": "s2", "to": "f2", "to_port": "out"}), "to_port 无效"),
        (lambda s: s.update({"outputs": {"entry": "x1"}}), "outputs"),
        (lambda s: s.update({"outputs": {"entry": "f1", "exit": "x2"}}), "布尔节点"),
        (
            lambda s: s.update(
                {"nodes": [{"id": "s1", "type": "close"}, {"id": "x1", "type": "and"}], "edges": [], "outputs": {"entry": "x1", "exit": "x1"}}
            ),
            "缺少输入",
        ),
        (
            lambda s: s.update(
                {
                    "nodes": [{"id": f"n{i:02d}", "type": "close"} for i in range(49)],
                    "outputs": {"entry": "n00", "exit": "n01"},
                }
            ),
            "最多",
        ),
    ],
)
def test_parse_v2_rejects(mutate, keyword):
    with pytest.raises(ValueError, match=keyword):
        parse_spec(graph_variant(mutate))


def test_parse_v2_rejects_cycle():
    raw = graph_variant(
        lambda s: s.update(
            {
                "nodes": [
                    s["nodes"][0],  # a=close
                    s["nodes"][1],  # s2=close
                    {"id": "b", "type": "sub"},
                    {"id": "d", "type": "add"},
                    {"id": "g1", "type": "gt"},
                ],
                "edges": [
                    {"from": "s1", "to": "b", "to_port": "left"},
                    {"from": "d", "to": "b", "to_port": "right"},  # b ← d，d ← b：环
                    {"from": "b", "to": "d", "to_port": "left"},
                    {"from": "s2", "to": "d", "to_port": "right"},
                    {"from": "b", "to": "g1", "to_port": "left"},
                    {"from": "s2", "to": "g1", "to_port": "right"},
                ],
                "outputs": {"entry": "g1", "exit": "g1"},
            }
        )
    )
    with pytest.raises(ValueError, match="环"):
        parse_spec(raw)


def test_parse_v2_bool_into_value_port():
    raw = {
        "version": 2,
        "nodes": [
            {"id": "s1", "type": "close"},
            {"id": "g1", "type": "gt"},
            {"id": "g2", "type": "gt"},
            {"id": "m1", "type": "and"},
        ],
        "edges": [
            {"from": "s1", "to": "g1", "to_port": "left"},
            {"from": "s1", "to": "g1", "to_port": "right"},
            {"from": "g1", "to": "g2", "to_port": "left"},  # 布尔 → 数值端口
            {"from": "s1", "to": "g2", "to_port": "right"},
            {"from": "g1", "to": "m1", "to_port": "left"},
            {"from": "g2", "to": "m1", "to_port": "right"},
        ],
        "outputs": {"entry": "m1", "exit": "m1"},
    }
    with pytest.raises(ValueError, match="布尔"):
        parse_spec(raw)


def _slide_signals(spec_raw, closes, min_rows):
    """同一起点滑窗逐 bar 求信号：返回 (entry 触发索引, exit 触发索引)。v1/v2 通用。"""
    parsed = parse_spec(spec_raw)
    frame = _frame(closes)
    entry_fired, exit_fired = [], []
    for i in range(min_rows, len(closes)):
        window = frame.iloc[i - min_rows + 1 : i + 1]
        if isinstance(parsed, GraphSpec):
            e, x = graph_signals(window, parsed)
        else:
            cache: dict = {}
            e = all(_condition_series(window, c, cache).iloc[-1] for c in parsed.entry)
            x = any(_condition_series(window, c, cache).iloc[-1] for c in parsed.exit)
        if e:
            entry_fired.append(i)
        if x:
            exit_fired.append(i)
    return entry_fired, exit_fired


CROSS_CLOSES = [10.0] * 6 + [12.0, 13.2, 14.4, 15.6, 16.8, 18.0] + [17.0, 16.0, 15.0, 14.0, 13.0] + [14.0, 16.0, 18.0, 20.0]


def test_v2_cross_graph_matches_v1_conditions():
    """金叉/死叉 graph 与 v1 条件数组在同一数据、同一最小行数下逐点一致。"""
    small_graph = {
        "version": 2,
        "nodes": [
            {"id": "s1", "type": "close"},
            {"id": "s2", "type": "close"},
            {"id": "f1", "type": "sma", "params": {"period": 3}},
            {"id": "f2", "type": "sma", "params": {"period": 6}},
            {"id": "x1", "type": "cross_above"},
            {"id": "x2", "type": "cross_below"},
        ],
        "edges": [
            {"from": "s1", "to": "f1", "to_port": "in"},
            {"from": "s2", "to": "f2", "to_port": "in"},
            {"from": "f1", "to": "x1", "to_port": "left"},
            {"from": "f2", "to": "x1", "to_port": "right"},
            {"from": "f1", "to": "x2", "to_port": "left"},
            {"from": "f2", "to": "x2", "to_port": "right"},
        ],
        "outputs": {"entry": "x1", "exit": "x2"},
    }
    v1_spec = {
        "entry": [
            {
                "type": "cross_above",
                "left": {"kind": "indicator", "name": "sma", "params": {"period": 3}},
                "right": {"kind": "indicator", "name": "sma", "params": {"period": 6}},
            }
        ],
        "exit": [
            {
                "type": "cross_below",
                "left": {"kind": "indicator", "name": "sma", "params": {"period": 3}},
                "right": {"kind": "indicator", "name": "sma", "params": {"period": 6}},
            }
        ],
    }
    v1_rows = spec_lookback(parse_spec(v1_spec))
    v2_rows = graph_lookback(parse_spec(small_graph))
    assert v1_rows == v2_rows  # 预热对齐：6+2 == (6+1)+1
    entry_v1, exit_v1 = _slide_signals(v1_spec, CROSS_CLOSES, v1_rows)
    entry_v2, exit_v2 = _slide_signals(small_graph, CROSS_CLOSES, v2_rows)
    assert entry_v1 == entry_v2 == [19]  # 下跌后二次上行的金叉（首个金叉在预热窗口内，不触发）
    assert exit_v1 == exit_v2 == [14]  # 下跌段唯一的死叉


def test_v2_and_logic_graph():
    """AND/比较/常数/动量/RSI 组合入场，恒真离场；与 v1 组合条件逐点一致。"""
    closes = [100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0, 92.0, 91.0, 93.0, 95.0, 97.0, 99.0, 101.0, 103.0]
    graph = {
        "version": 2,
        "nodes": [
            {"id": "s1", "type": "close"},
            {"id": "m1", "type": "momentum", "params": {"period": 3}},
            {"id": "k1", "type": "const", "value": -0.01},
            {"id": "g1", "type": "lt"},
            {"id": "r1", "type": "rsi", "params": {"period": 6}},
            {"id": "k2", "type": "const", "value": 40},
            {"id": "g2", "type": "lt"},
            {"id": "a1", "type": "and"},
            {"id": "s2", "type": "close"},
            {"id": "k3", "type": "const", "value": 0},
            {"id": "g3", "type": "gt"},
        ],
        "edges": [
            {"from": "s1", "to": "m1", "to_port": "in"},
            {"from": "m1", "to": "g1", "to_port": "left"},
            {"from": "k1", "to": "g1", "to_port": "right"},
            {"from": "s1", "to": "r1", "to_port": "in"},
            {"from": "r1", "to": "g2", "to_port": "left"},
            {"from": "k2", "to": "g2", "to_port": "right"},
            {"from": "g1", "to": "a1", "to_port": "left"},
            {"from": "g2", "to": "a1", "to_port": "right"},
            {"from": "s2", "to": "g3", "to_port": "left"},
            {"from": "k3", "to": "g3", "to_port": "right"},
        ],
        "outputs": {"entry": "a1", "exit": "g3"},
    }
    v1_spec = {
        "entry": [
            {"type": "lt", "left": {"kind": "indicator", "name": "momentum", "params": {"period": 3}}, "right": {"kind": "const", "value": -0.01}},
            {"type": "lt", "left": {"kind": "indicator", "name": "rsi", "params": {"period": 6}}, "right": {"kind": "const", "value": 40}},
        ],
        "exit": [{"type": "gt", "left": {"kind": "field", "field": "close"}, "right": {"kind": "const", "value": 0}}],
    }
    min_rows = max(spec_lookback(parse_spec(v1_spec)), graph_lookback(parse_spec(graph)))
    entry_v1, exit_v1 = _slide_signals(v1_spec, closes, min_rows)
    entry_v2, exit_v2 = _slide_signals(graph, closes, min_rows)
    assert entry_v2 == entry_v1 and entry_v2  # 连跌尾段：动量负 + RSI 极低
    assert exit_v2 == exit_v1 and exit_v2  # close>0 恒真，每个评估 bar 触发


def test_v2_graph_equivalent_to_dual_ma(context):
    """v2 图（sma 金叉/死叉 5/20）与内置 dual_ma 的买入交易日一致。"""
    graph = dict(GOLDEN_GRAPH_SPEC)
    custom = BacktestEngine(
        EngineConfig(
            strategy_id=STRATEGY_ID,
            symbols=["600519"],
            start=date(2023, 1, 1),
            end=date(2024, 6, 30),
            initial_cash=1_000_000,
            params={"spec": graph},
            cost={},
            run_id="run_graph1",
        ),
        context.data,
    ).run()
    builtin = BacktestEngine(
        EngineConfig(
            strategy_id="dual_ma",
            symbols=["600519"],
            start=date(2023, 1, 1),
            end=date(2024, 6, 30),
            initial_cash=1_000_000,
            params={"fast": 5, "slow": 20, "position_pct": 0.9, "stop_loss": 0.08, "take_profit": 0.0},
            cost={},
            run_id="run_builtin2",
        ),
        context.data,
    ).run()
    assert custom.status == "finished" and builtin.status == "finished"
    builtin_days = [t["dt"][:10] for t in builtin.trades if t["side"] == "buy"]
    graph_days = [t["dt"][:10] for t in custom.trades if t["side"] == "buy"]
    assert graph_days == builtin_days
    assert graph_days, "合成行情应产生交易"


def test_v2_save_and_run_via_api(client):
    headers = login_headers(client)
    resp = client.post("/api/strategies/custom", json={"name": "蓝图金叉", "spec": GOLDEN_GRAPH_SPEC}, headers=headers)
    assert resp.status_code == 201, resp.text
    sid = resp.json()["id"]
    resp = client.get("/api/strategies/custom", headers=headers)
    assert resp.json()[0]["spec"]["version"] == 2
    resp = client.post(
        "/api/backtests",
        json={
            "strategy_id": "custom_rule",
            "symbols": ["600519"],
            "start": "2023-01-01",
            "end": "2024-06-30",
            "params": {"custom_strategy_id": sid},
        },
        headers=headers,
    )
    assert resp.status_code == 202
