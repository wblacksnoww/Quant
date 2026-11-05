"""策略元信息里的方向语义。

做多/做空不是两套策略：``direction=short`` 由 ``StrategyContext.submit`` 统一翻转
买卖方向，所以每个策略都「能」做空，但翻转后的含义随策略而异——趋势策略翻成逆势、
动量轮动翻成做空领涨。策略库要讲清楚这一点，否则用户看到的只是一句「金叉买入」。

守的口径：

1. 内置策略必须同时给出 ``long_note`` 与 ``short_note``，不能靠前端猜。
2. ``long_note`` 留空时回落到 ``description``（用户自定义策略不改也能用）。
3. ``short_note`` 留空时回落到框架的真实行为说明，不是空字符串。
4. API ``/api/strategies`` 必须把这些字段透出去——存了没透等于没做。

注意：注册表是全局的，其它测试会注册无描述的临时策略，所以这里只断言
``quant.strategy.builtin`` 包里的类，不要对 ``list_strategies()`` 全量断言。
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import quant.strategy.builtin as builtin_pkg
from quant.strategy.base import DEFAULT_SHORT_NOTE, Strategy
from quant.strategy.registry import list_strategies


def _builtin_classes() -> list[type[Strategy]]:
    classes: list[type[Strategy]] = []
    for info in pkgutil.iter_modules(builtin_pkg.__path__):
        module = importlib.import_module(f"{builtin_pkg.__name__}.{info.name}")
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, Strategy)
                and obj is not Strategy
                and obj.__module__ == module.__name__
                and obj.strategy_id
            ):
                classes.append(obj)
    return classes


def test_builtin_strategies_document_both_directions():
    classes = _builtin_classes()
    assert len(classes) >= 5, f"内置策略只有 {len(classes)} 个，发现逻辑可能坏了"
    for cls in classes:
        assert cls.long_note.strip(), f"{cls.strategy_id} 缺少做多说明"
        assert cls.short_note.strip(), f"{cls.strategy_id} 缺少做空说明"


def test_builtin_short_notes_are_strategy_specific():
    """内置策略不能全都退化成默认说明——那说明压根没写。"""
    defaulted = [cls.strategy_id for cls in _builtin_classes() if cls.short_note == DEFAULT_SHORT_NOTE]
    assert not defaulted, f"这些策略没写做空说明：{defaulted}"


def test_notes_differ_between_directions():
    for cls in _builtin_classes():
        schema = cls.schema()
        assert schema["long_note"] != schema["short_note"], cls.strategy_id


def test_short_note_mentions_short_side():
    """做空说明要能看出讲的是空头，别写成泛泛的风险提示。"""
    for cls in _builtin_classes():
        assert "空" in cls.short_note, cls.strategy_id


def test_default_short_note_describes_flip():
    assert "反向" in DEFAULT_SHORT_NOTE or "翻转" in DEFAULT_SHORT_NOTE


def test_schema_falls_back_when_notes_missing():
    class Bare(Strategy):
        strategy_id = "bare"
        description = "只有一句话描述"

    schema = Bare.schema()
    assert schema["long_note"] == "只有一句话描述"
    assert schema["short_note"] == DEFAULT_SHORT_NOTE


def test_api_exposes_direction_notes(client):
    login = client.post("/api/auth/login", json={"username": "admin", "password": "Test!Passw0rd"})
    assert login.status_code == 200
    token = login.json()["access_token"]

    response = client.get("/api/strategies", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()}
    for cls in _builtin_classes():
        item = items.get(cls.strategy_id)
        if item is None:  # 注册表里没被发现的策略不在这里断言
            continue
        assert item["long_note"] == cls.long_note
        assert item["short_note"] == cls.short_note
    assert items["custom_rule"]["short_note"] == DEFAULT_SHORT_NOTE


def test_list_strategies_never_returns_blank_notes():
    for schema in list_strategies():
        assert schema["short_note"].strip(), schema["id"]
