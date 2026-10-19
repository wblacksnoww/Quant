"""指数行情：解析、缓存与失败保留、路由契约。"""

from __future__ import annotations

import pytest

from conftest import login_headers
from quant.data import indices as idx


class _Resp:
    def __init__(self, text: str):
        self.text = text
        self.encoding = "gbk"

    def raise_for_status(self):
        return None


def _line(code: str, name: str, price: float, prev: float, chg: float, pct: float) -> str:
    fields = ["0"] * 33
    fields[0] = "1"
    fields[1] = name
    fields[3] = f"{price}"
    fields[4] = f"{prev}"
    fields[31] = f"{chg}"
    fields[32] = f"{pct}"
    return f'v_{code}="' + "~".join(fields) + '";'


def test_fetch_indices_parses_all_markets(monkeypatch):
    lines = {
        "sh000001": _line("sh000001", "上证指数", 3730.92, 3716.28, 14.64, 0.39),
        "hkHSI": _line("hkHSI", "恒生指数", 24186.83, 24062.72, 124.11, 0.52),
        "usDJI": _line("usDJI", "道琼斯", 52002.41, 52177.69, -175.28, -0.34),
    }

    def fake_get(url, *a, **k):
        codes = url.split("q=")[-1].split(",")
        return _Resp("\n".join(lines[code] for code in codes if code in lines))

    monkeypatch.setattr(idx.requests, "get", fake_get)
    quotes = idx.fetch_indices()
    assert [q.id for q in quotes] == ["sh000001", "hkHSI", "usDJI"]
    assert quotes[0].price == pytest.approx(3730.92)
    assert quotes[0].change_pct == pytest.approx(0.39)
    assert quotes[1].currency == "HKD"
    assert quotes[2].market == "US"

    # 只取指定指数
    only = idx.fetch_indices(["hkHSI"])
    assert [q.id for q in only] == ["hkHSI"]


def test_service_indices_keeps_last_on_failure(monkeypatch):
    from quant.data.store import MarketDataService

    calls = {"n": 0}

    def fake_fetch(ids=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return [idx.IndexQuote(id="sh000001", name="上证指数", market="CN", currency="CNY", price=3730.92, change_pct=0.39)]
        raise idx.DataProviderUnavailable("down")

    monkeypatch.setattr(idx, "fetch_indices", fake_fetch)
    service = MarketDataService(store=None, provider=object())  # type: ignore[arg-type]

    first = service.indices(ttl=0)
    assert first[0]["price"] == pytest.approx(3730.92)
    # 数据源失败时返回上一次结果，不清空卡片
    second = service.indices(ttl=0)
    assert second == first


def test_indices_route_contract(client, context, monkeypatch):
    monkeypatch.setattr(
        context.data,
        "indices",
        lambda ttl=5.0: [
            {
                "id": "sh000001",
                "name": "上证指数",
                "market": "CN",
                "currency": "CNY",
                "price": 3730.92,
                "prev_close": 3716.28,
                "change": 14.64,
                "change_pct": 0.39,
            }
        ],
    )
    resp = client.get("/api/market/indices", headers=login_headers(client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body[0]["id"] == "sh000001"
    assert body[0]["change_pct"] == pytest.approx(0.39)
