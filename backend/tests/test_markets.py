"""多市场基础：符号体系、交易规则、成本模型、缓存命名迁移、标的检索解析。"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from quant.core.markets import (
    Instrument,
    Market,
    currency_symbol,
    parse_instrument,
    single_market,
    strip_us_suffix,
    try_parse,
)
from quant.core.types import Side
from quant.data.base import SymbolInfo, create_provider
from quant.data.search import parse_smartbox, search_symbols
from quant.data.store import DataStore
from quant.engine.base import EngineConfig
from quant.execution.cost import CostModel
from quant.execution.rules import (
    board_of,
    is_valid_lot,
    price_limits,
    round_down_lot,
    rules_for,
)

# --------------------------------------------------------------------------- #
# 符号体系
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("600519", "CN:600519"),
        ("sh600519", "CN:600519"),
        ("SH.600519", "CN:600519"),
        ("600519.SH", "CN:600519"),
        ("000001", "CN:000001"),
        ("000001.SZ", "CN:000001"),
        ("920930", "CN:920930"),
        ("aapl", "US:AAPL"),
        ("AAPL.US", "US:AAPL"),
        ("TSLA.N", "US:TSLA"),
        ("BRK.B", "US:BRK.B"),
        ("brk.b.n", "US:BRK.B"),
        ("700", "HK:00700"),
        ("00700", "HK:00700"),
        ("hk700", "HK:00700"),
        ("0700.HK", "HK:00700"),
        ("US:AAPL", "US:AAPL"),
        ("CN:600519", "CN:600519"),
        ("SHOP", "US:SHOP"),  # SH 前缀不得吞掉美股代码
        ("USX", "US:USX"),
    ],
)
def test_parse_instrument(text, expected):
    assert parse_instrument(text).id == expected


@pytest.mark.parametrize("bad", ["", "   ", "1234567", "!!!", "HK", "US:"])
def test_parse_instrument_invalid(bad):
    with pytest.raises(ValueError):
        parse_instrument(bad)
    assert try_parse(bad) is None


def test_strip_us_suffix():
    assert strip_us_suffix("AAPL.OQ") == "AAPL"
    assert strip_us_suffix("BRK.B.N") == "BRK.B"
    assert strip_us_suffix("TCEHY.PS") == "TCEHY"
    assert strip_us_suffix("BRK.B") == "BRK.B"


def test_single_market():
    assert single_market(["600519", "000001"]) is Market.CN
    assert single_market(["AAPL"]) is Market.US
    with pytest.raises(ValueError, match="单一市场"):
        single_market(["600519", "AAPL"])


def test_currency_symbol():
    assert currency_symbol("CNY") == "¥"
    assert currency_symbol("USD") == "$"
    assert currency_symbol("HKD") == "HK$"
    assert currency_symbol("EUR") == "EUR"


def test_symbol_info_to_dict():
    info = SymbolInfo(symbol="CN:600519", name="贵州茅台", board="沪深主板", market="CN")
    payload = info.to_dict()
    assert payload["code"] == "600519"
    assert payload["market"] == "CN"
    us = SymbolInfo(symbol="US:BRK.B", market="US", kind="stock")
    assert us.to_dict()["code"] == "BRK.B"


# --------------------------------------------------------------------------- #
# 交易规则
# --------------------------------------------------------------------------- #


def test_board_of_multi_market():
    assert board_of("600519").code == "main"
    assert board_of("300059").code == "gem"
    assert board_of("688111").code == "star"
    assert board_of("US:AAPL").code == "us"
    assert board_of("HK:00700").code == "hk"


def test_price_limits_multi_market():
    assert price_limits(100.0, "CN:600519") == (110.0, 90.0)
    assert price_limits(100.0, "US:AAPL") == (None, None)
    assert price_limits(100.0, "HK:00700") == (None, None)


def test_lot_rules_multi_market():
    assert rules_for("US:AAPL").lot_size == 1
    assert rules_for("US:AAPL").t_plus_1 is False
    assert is_valid_lot(1, "US:AAPL")
    assert is_valid_lot(37, "US:AAPL")
    assert round_down_lot(3.7, "US:AAPL") == 3

    assert rules_for("HK:00700").lot_size == 100
    assert rules_for("HK:00700").t_plus_1 is False
    assert is_valid_lot(200, "HK:00700")
    assert not is_valid_lot(150, "HK:00700")
    assert is_valid_lot(150, "HK:00700", is_full_exit=True)
    assert round_down_lot(250, "HK:00700") == 200


# --------------------------------------------------------------------------- #
# 成本模型
# --------------------------------------------------------------------------- #


def test_cost_model_market_presets():
    cn = CostModel.for_market("CN")
    assert cn.fees(Side.BUY, 100.0, 100).stamp_tax == 0.0
    assert cn.fees(Side.SELL, 100.0, 100).stamp_tax == pytest.approx(5.0)  # 0.05% 卖出

    us = CostModel.for_market("US")
    assert us.min_commission == 0.0
    fees = us.fees(Side.SELL, 100.0, 10)
    assert fees.stamp_tax == 0.0
    assert fees.commission == pytest.approx(0.1)

    hk = CostModel.for_market("HK")
    assert hk.stamp_both_sides is True
    assert hk.fees(Side.BUY, 100.0, 100).stamp_tax == pytest.approx(10.0)  # 0.1% 双边
    assert hk.fees(Side.SELL, 100.0, 100).stamp_tax == pytest.approx(10.0)


def test_cost_model_overrides_win():
    custom = CostModel.for_market("US", {"commission_rate": 0.002})
    assert custom.commission_rate == pytest.approx(0.002)
    assert CostModel.for_market("US", None).commission_rate == pytest.approx(1e-4)


# --------------------------------------------------------------------------- #
# 引擎配置
# --------------------------------------------------------------------------- #


def _config(symbols: list[str]) -> EngineConfig:
    return EngineConfig(
        strategy_id="dual_ma",
        symbols=symbols,
        start=date(2024, 1, 1),
        end=date(2024, 6, 1),
    )


def test_engine_config_normalizes_symbols():
    cfg = _config(["600519", "sh000001"])
    cfg.validate()
    assert cfg.symbols == ["CN:600519", "CN:000001"]
    assert cfg.market == "CN"

    us = _config(["AAPL"])
    us.validate()
    assert us.symbols == ["US:AAPL"]
    assert us.market == "US"


def test_engine_config_rejects_mixed_markets():
    with pytest.raises(ValueError, match="单一市场"):
        _config(["600519", "AAPL"]).validate()


# --------------------------------------------------------------------------- #
# 缓存命名
# --------------------------------------------------------------------------- #


def test_store_path_for_multi_market(tmp_path):
    store = DataStore(root=tmp_path, provider=create_provider("synthetic"))
    assert store.path_for("600519").name == "CN_600519_qfq.parquet"
    assert store.path_for("US:AAPL").name == "US_AAPL_qfq.parquet"
    assert store.path_for("700").name == "HK_00700_qfq.parquet"


def test_store_migrates_legacy_cache_names(tmp_path):
    legacy = tmp_path / "600519_qfq.parquet"
    pd.DataFrame({"date": pd.to_datetime(["2024-01-02"])}).to_parquet(legacy, index=False)

    store = DataStore(root=tmp_path, provider=create_provider("synthetic"))

    assert not legacy.exists()
    assert (tmp_path / "CN_600519_qfq.parquet").exists()
    assert store.cached_symbols() == ["CN:600519"]
    stats = store.stats()
    assert stats[0]["symbol"] == "CN:600519"
    assert stats[0]["code"] == "600519"
    assert stats[0]["market"] == "CN"


# --------------------------------------------------------------------------- #
# 标的检索（smartbox 解析）
# --------------------------------------------------------------------------- #

SMARTBOX_TEXT = (
    'v_hint="sz~159915~创业板ETF易方达~cybetfyfd~ETF'
    "^sh~600519~贵州茅台~gzmt~GP-A"
    "^hk~00700~腾讯控股~txkg~GP"
    "^us~aapl.oq~苹果~pg~GP"
    "^us~brk.b.n~伯克希尔b~bkxeb~GP"
    "^jj~007005~中金新医药股票C~zjxyygpc~KJ"
    '^sh~000001~上证指数~szzs~ZS";'
)


def test_parse_smartbox():
    items = parse_smartbox(SMARTBOX_TEXT)
    ids = [item.symbol for item in items]
    # 场外基金（jj/KJ）与指数（ZS）被过滤
    assert ids == ["CN:159915", "CN:600519", "HK:00700", "US:AAPL", "US:BRK.B"]

    by_id = {item.symbol: item for item in items}
    assert by_id["CN:159915"].kind == "etf"
    assert by_id["CN:600519"].kind == "stock"
    assert by_id["CN:600519"].name == "贵州茅台"
    assert by_id["CN:600519"].board == "沪深主板"
    assert by_id["HK:00700"].board == "港股"
    assert by_id["US:AAPL"].name == "苹果"


def test_search_symbols_filters_market(monkeypatch):
    class _Resp:
        text = SMARTBOX_TEXT
        encoding = "utf-8"

        def raise_for_status(self):
            return None

    monkeypatch.setattr("quant.data.search.requests.get", lambda *a, **k: _Resp())
    only_hk = search_symbols("腾讯", market="HK", limit=10)
    assert [item.symbol for item in only_hk] == ["HK:00700"]
    everything = search_symbols("a", limit=3)
    assert len(everything) == 3


# --------------------------------------------------------------------------- #
# API 契约
# --------------------------------------------------------------------------- #


def test_market_endpoints_contract(client):
    from conftest import login_headers

    headers = login_headers(client)

    markets = client.get("/api/market/markets", headers=headers)
    assert markets.status_code == 200, markets.text
    payload = markets.json()
    assert {m["code"] for m in payload} == {"CN", "US", "HK"}
    assert {m["currency"] for m in payload} == {"CNY", "USD", "HKD"}

    bars = client.get("/api/market/bars", params={"symbol": "CN:600519"}, headers=headers)
    assert bars.status_code == 200, bars.text
    body = bars.json()
    assert body["symbol"] == "CN:600519"
    assert body["market"] == "CN"
    assert body["currency"] == "CNY"
    assert body["bars"]

    bad = client.get("/api/market/bars", params={"symbol": "@@@"}, headers=headers)
    assert bad.status_code == 422

    calendar = client.get("/api/market/calendar", params={"market": "US"}, headers=headers)
    assert calendar.status_code == 200
    assert all(date.fromisoformat(d).weekday() < 5 for d in calendar.json())
