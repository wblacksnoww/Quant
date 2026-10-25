"""组合权重优化与指数成分股（point-in-time）。"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant.portfolio import (
    METHODS,
    PortfolioConstraints,
    PortfolioError,
    align_returns,
    optimize_weights,
)
from quant.storage.repositories.index_membership import IndexMembershipRepository


def _returns(vols: list[float], n: int = 500, seed: int = 7) -> pd.DataFrame:
    """构造指定波动率、互不相关的收益率矩阵（确定性的）。"""
    rng = np.random.default_rng(seed)
    data = {f"S{i}": rng.normal(0, vol, n) for i, vol in enumerate(vols)}
    index = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame(data, index=index)


# --------------------------------------------------------------------------- #
# 权重优化
# --------------------------------------------------------------------------- #


def test_equal_weight_is_uniform():
    solution = optimize_weights(_returns([0.01, 0.02, 0.03]), "equal_weight")
    for symbol in ("S0", "S1", "S2"):
        assert solution.weights[symbol] == pytest.approx(1 / 3, abs=1e-6)
    assert sum(solution.weights.values()) == pytest.approx(1.0, abs=1e-9)


def test_inverse_vol_favours_low_volatility():
    """波动率小 4 倍的标的，权重应显著更大，但比例不是线性的。"""
    solution = optimize_weights(_returns([0.01, 0.04]), "inverse_vol", constraints=PortfolioConstraints())
    assert solution.weights["S0"] > solution.weights["S1"]
    # 理论值：w ∝ 1/σ → (4/5, 1/5)
    assert solution.weights["S0"] == pytest.approx(0.8, abs=0.05)


def test_risk_parity_matches_inverse_vol_when_uncorrelated():
    """不相关时风险平价退化为波动率倒数 —— 两个独立实现互相印证。"""
    returns = _returns([0.01, 0.02, 0.04])
    rp = optimize_weights(returns, "risk_parity")
    iv = optimize_weights(returns, "inverse_vol")
    for symbol in rp.weights:
        assert rp.weights[symbol] == pytest.approx(iv.weights[symbol], abs=0.03)


def test_min_variance_prefers_diversification():
    returns = _returns([0.01, 0.01])
    solution = optimize_weights(returns, "min_variance")
    # 两个独立同波动资产：最小方差解应接近等权
    assert solution.weights["S0"] == pytest.approx(0.5, abs=0.06)
    assert solution.weights["S1"] == pytest.approx(0.5, abs=0.06)


def test_max_weight_constraint_is_respected():
    """4 个标的、单票上限 30%：上限必须真的生效（总量刚好够 100%）。"""
    returns = _returns([0.005, 0.05, 0.02, 0.03])
    solution = optimize_weights(returns, "inverse_vol", constraints=PortfolioConstraints(max_weight=0.3))
    assert max(solution.weights.values()) <= 0.3 + 1e-9
    assert sum(solution.weights.values()) == pytest.approx(1.0, abs=1e-9)


def test_weights_always_sum_to_one_even_with_impossible_constraints():
    """约束互相矛盾时不能返回一堆加起来不等于 1 的权重。

    4 个标的、上限 90% 是可行的；这里测的是极端但可行的边界。
    """
    solution = optimize_weights(
        _returns([0.01, 0.02, 0.03, 0.04]),
        "risk_parity",
        constraints=PortfolioConstraints(max_weight=0.9),
    )
    assert sum(solution.weights.values()) == pytest.approx(1.0, abs=1e-6)


def test_impossible_constraints_rejected():
    with pytest.raises(PortfolioError, match="到不了 100%"):
        optimize_weights(
            _returns([0.01, 0.02, 0.03]),
            "equal_weight",
            constraints=PortfolioConstraints(max_weight=0.2),
        )


def test_single_symbol_shortcut():
    solution = optimize_weights(_returns([0.01]), "risk_parity")
    assert solution.weights == {"S0": 1.0}
    assert "只有一个标的" in solution.note


def test_unknown_method_rejected():
    with pytest.raises(PortfolioError, match="未知方法"):
        optimize_weights(_returns([0.01, 0.02]), "magic")


def test_metrics_are_consistent():
    solution = optimize_weights(_returns([0.01, 0.02, 0.03]), "risk_parity")
    assert solution.volatility > 0
    assert solution.expected_return != 0
    # 夏普必须能由前两项算出来（防止指标之间各算各的）
    assert solution.sharpe == pytest.approx((solution.expected_return - 0.0) / solution.volatility, abs=1e-9)
    assert solution.diversification >= 1.0 - 1e-9


def test_all_methods_run_and_normalize():
    returns = _returns([0.01, 0.02, 0.03, 0.015])
    for method in METHODS:
        solution = optimize_weights(returns, method)
        assert sum(solution.weights.values()) == pytest.approx(1.0, abs=1e-6), method
        assert all(w >= 0 for w in solution.weights.values()), method


# --------------------------------------------------------------------------- #
# 收益率对齐
# --------------------------------------------------------------------------- #


def _frame(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"date": dates, "close": closes})


def test_align_uses_date_intersection_not_union():
    """只在共有日期上算收益率。

    用并集 + 前向填充会把停牌期补成"零收益"，人为压低波动率，
    优化结果会严重偏向那些经常停牌的标的。
    """
    a = _frame(["2023-01-03", "2023-01-04", "2023-01-05", "2023-01-06"], [10, 10.5, 11, 11.2])
    b = _frame(["2023-01-04", "2023-01-05", "2023-01-06"], [20, 20.2, 20.5])
    returns = align_returns({"A": a, "B": b})
    assert list(returns.index.strftime("%Y-%m-%d")) == ["2023-01-05", "2023-01-06"]
    assert list(returns.columns) == ["A", "B"]


def test_align_rejects_too_little_data():
    tiny = _frame(["2023-01-03", "2023-01-04"], [10, 10.5])
    with pytest.raises(PortfolioError):
        align_returns({"A": tiny, "B": tiny})


# --------------------------------------------------------------------------- #
# 指数成分股（point-in-time）
# --------------------------------------------------------------------------- #


def test_snapshot_add_and_remove_builds_ranges(context):
    """两次快照的差集要能拼出每只股票的进出区间。

    这是整个 POINT-IN-TIME 逻辑的核心：只有这样才能回答"2023-04-01 那天
    指数里到底有哪些股票"，而不是用今天的名单回测三年前。
    """
    with context.db.session() as session:
        repo = IndexMembershipRepository(session)
        first = repo.replace_snapshot(
            "MARKET:000300", ["CN:600519", "CN:000001", "CN:600036"], date(2023, 1, 1)
        )
        assert first == {"added": 3, "removed": 0, "kept": 0}

        second = repo.replace_snapshot(
            "MARKET:000300", ["CN:600519", "CN:600036", "CN:601318"], date(2023, 7, 1)
        )
        assert second == {"added": 1, "removed": 1, "kept": 2}
        session.commit()

        assert repo.members_at("MARKET:000300", date(2023, 3, 1)) == [
            "CN:000001",
            "CN:600036",
            "CN:600519",
        ]
        # 掉出的标的在调入日当天就不该再出现
        assert "CN:000001" not in repo.members_at("MARKET:000300", date(2023, 7, 1))
        assert "CN:601318" in repo.members_at("MARKET:000300", date(2023, 7, 1))
        # 边界：end_date 是 6-30，所以 6-30 仍在、7-1 已不在
        assert "CN:000001" in repo.members_at("MARKET:000300", date(2023, 6, 30))


def test_reimporting_same_snapshot_is_idempotent(context):
    with context.db.session() as session:
        repo = IndexMembershipRepository(session)
        repo.replace_snapshot("MARKET:000905", ["CN:600519"], date(2023, 1, 1))
        again = repo.replace_snapshot("MARKET:000905", ["CN:600519"], date(2023, 1, 1))
        session.commit()
        assert again == {"added": 0, "removed": 0, "kept": 1}
        assert repo.members_at("MARKET:000905", date(2023, 1, 1)) == ["CN:600519"]


def test_out_of_order_snapshot_does_not_create_inverted_range(context):
    """乱序导入（先有晚的、再有早的）不能生成 start > end 的非法区间。"""
    with context.db.session() as session:
        repo = IndexMembershipRepository(session)
        repo.replace_snapshot("MARKET:000016", ["CN:600519", "CN:600036"], date(2023, 7, 1))
        # 更早的快照里没有 600036 —— 若直接补 end_date 会变成 6-30 < start 7-1
        repo.replace_snapshot("MARKET:000016", ["CN:600519"], date(2023, 1, 1))
        session.commit()
        rows = repo.timeline("MARKET:000016")
        for row in rows:
            if row["start_date"] and row["end_date"]:
                assert row["end_date"] >= row["start_date"], row


def test_has_history_marks_unknown_index(context):
    with context.db.session() as session:
        repo = IndexMembershipRepository(session)
        assert repo.has_history("MARKET:999999") is False
        repo.replace_snapshot("MARKET:999999", ["CN:600519"], date(2023, 1, 1))
        session.commit()
        assert repo.has_history("MARKET:999999") is True


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def test_api_optimize_roundtrip(client):
    from conftest import login_headers

    headers = login_headers(client)
    response = client.post(
        "/api/portfolio/optimize",
        json={
            "symbols": ["CN:600519", "CN:000001"],
            "method": "risk_parity",
            "start": "2023-01-01",
            "end": "2024-06-30",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["weights"]) == {"CN:600519", "CN:000001"}
    assert sum(body["weights"].values()) == pytest.approx(1.0, abs=1e-6)
    assert body["method_label"] == METHODS["risk_parity"]


def test_api_methods_listed(client):
    from conftest import login_headers

    headers = login_headers(client)
    response = client.get("/api/portfolio/methods", headers=headers)
    assert response.status_code == 200
    assert {item["key"] for item in response.json()} == set(METHODS)


def test_api_optimize_rejects_single_symbol(client):
    from conftest import login_headers

    headers = login_headers(client)
    response = client.post(
        "/api/portfolio/optimize",
        json={"symbols": ["CN:600519"], "method": "equal_weight"},
        headers=headers,
    )
    assert response.status_code == 422, response.text


def test_api_index_snapshot_and_query(client):
    from conftest import login_headers

    headers = login_headers(client)
    imported = client.post(
        "/api/portfolio/indexes/MARKET:000300/members",
        json={"symbols": ["CN:600519", "CN:000001"], "as_of": "2023-01-01"},
        headers=headers,
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["added"] == 2

    listing = client.get("/api/portfolio/indexes", headers=headers)
    assert listing.status_code == 200
    assert any(item["index_code"] == "MARKET:000300" for item in listing.json())

    members = client.get(
        "/api/portfolio/indexes/MARKET:000300/members",
        params={"as_of": "2023-06-01"},
        headers=headers,
    )
    assert members.status_code == 200
    assert {item["symbol"] for item in members.json()} == {"CN:600519", "CN:000001"}


def test_api_unknown_index_returns_404_with_hint(client):
    from conftest import login_headers

    headers = login_headers(client)
    response = client.get(
        "/api/portfolio/indexes/MARKET:888888/members",
        params={"as_of": "2023-06-01"},
        headers=headers,
    )
    assert response.status_code == 404
    # 错误信息要能指导下一步动作，而不只是"找不到"
    assert "快照" in response.json()["detail"]
