"""新闻/政策因子、ExitPolicy 与新闻策略。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from quant.engine import BacktestEngine, EngineConfig
from quant.news.factors import NewsFactor, compute_factor
from quant.strategy.exits import ExitDecision, ExitReason, pick_exit


class FakeNews:
    def __init__(self, factor: NewsFactor) -> None:
        self._factor = factor

    def factor(self, symbol: str, *, as_of, window: str = "1h", half_life_hours: float = 24.0):
        return self._factor


def _config(strategy: str, run_id: str, **params) -> EngineConfig:
    return EngineConfig(
        strategy_id=strategy,
        symbols=["CN:600519"],
        start=date(2023, 1, 1),
        end=date(2024, 6, 30),
        initial_cash=1_000_000,
        params=params,
        run_id=run_id,
    )


def test_news_factor_is_point_in_time_and_windowed():
    now = datetime(2026, 10, 21, 0, 0)
    items = [
        {
            "symbols": ["CRYPTO:BTC/USDT"],
            "effective_at": (now - timedelta(minutes=10)).isoformat(),
            "sentiment": 0.8,
            "policy_risk": 0.2,
            "confidence": 0.9,
        },
        {
            "symbols": ["CRYPTO:BTC/USDT"],
            "effective_at": (now - timedelta(hours=3)).isoformat(),
            "sentiment": -0.9,
            "policy_risk": 1.0,
            "confidence": 0.9,
        },
        {
            "symbols": ["CRYPTO:BTC/USDT"],
            "effective_at": (now + timedelta(minutes=10)).isoformat(),
            "sentiment": -1.0,
            "policy_risk": 1.0,
            "confidence": 1.0,
        },
    ]
    factor = compute_factor(items, symbol="CRYPTO:BTC/USDT", as_of=now, window="1h")
    assert factor.event_count == 1
    assert factor.sentiment == 0.8
    assert abs(factor.policy_risk - 0.18) < 1e-9
    assert factor.confidence == 0.9


def test_exit_policy_picks_single_highest_priority_action():
    decisions = [
        ExitDecision("reduce", ExitReason.SIGNAL, fraction=0.5, priority=50),
        ExitDecision("close", ExitReason.STOP_LOSS, priority=100),
        ExitDecision("close", ExitReason.TAKE_PROFIT, priority=80),
    ]
    picked = pick_exit(decisions)
    assert picked is not None
    assert picked.reason == ExitReason.STOP_LOSS
    assert pick_exit([ExitDecision("hold", ExitReason.SIGNAL)]) is None


def test_news_strategy_requires_news_for_entry(context):
    neutral = NewsFactor.neutral("CN:600519", datetime(2023, 1, 3))
    blocked = BacktestEngine(
        _config(
            "news_dual_ma",
            "run_news_block",
            fast=5,
            slow=20,
            require_news=True,
            min_confidence=0.5,
        ),
        context.data,
        news=FakeNews(neutral),
    ).run()
    assert blocked.status == "finished"
    assert blocked.metrics.trade_count == 0

    positive = NewsFactor(
        symbol="CN:600519",
        as_of=datetime(2023, 1, 3),
        window="1d",
        sentiment=0.5,
        policy_risk=0.0,
        confidence=1.0,
        event_count=1,
    )
    allowed = BacktestEngine(
        _config(
            "news_dual_ma",
            "run_news_allow",
            fast=5,
            slow=20,
            require_news=True,
            min_confidence=0.5,
            min_sentiment=0.0,
            max_policy_risk=0.5,
        ),
        context.data,
        news=FakeNews(positive),
    ).run()
    assert allowed.status == "finished"
    assert allowed.metrics.trade_count >= 1


def test_news_dual_ma_without_provider_matches_dual_ma(context):
    builtin = BacktestEngine(_config("dual_ma", "run_plain", fast=5, slow=20), context.data).run()
    news = BacktestEngine(_config("news_dual_ma", "run_news_plain", fast=5, slow=20), context.data).run()
    assert [t["dt"] for t in builtin.trades if t["side"] == "buy"] == [
        t["dt"] for t in news.trades if t["side"] == "buy"
    ]


def test_news_api_ingest_and_factor(client):
    from conftest import login_headers

    headers = login_headers(client)
    resp = client.post(
        "/api/news/items",
        json={
            "title": "监管发布加密资产风险提示",
            "summary": "政策风险上升",
            "symbols": ["CRYPTO:BTC/USDT"],
            "event_type": "policy",
            "sentiment": -0.7,
            "policy_risk": 0.85,
            "confidence": 0.9,
            "published_at": "2026-10-20T12:00:00Z",
            "effective_at": "2026-10-20T12:00:00Z",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    item = resp.json()
    assert item["symbols"] == ["CRYPTO:BTC/USDT"]

    listing = client.get("/api/news/items", params={"symbol": "CRYPTO:BTC/USDT"}, headers=headers)
    assert listing.status_code == 200
    assert listing.json()[0]["title"] == "监管发布加密资产风险提示"

    factor = client.get(
        "/api/news/factors",
        params={
            "symbol": "CRYPTO:BTC/USDT",
            "window": "4h",
            "as_of": "2026-10-20T13:00:00Z",
        },
        headers=headers,
    )
    assert factor.status_code == 200, factor.text
    data = factor.json()
    assert data["event_count"] == 1
    assert data["policy_risk"] == 0.765  # 0.85 * 0.9
    assert data["sentiment"] < 0

    # 同一标题+发布时间重复入库应触发唯一约束（由 raw_hash 拦截）
    duplicate = client.post(
        "/api/news/items",
        json={
            "title": "监管发布加密资产风险提示",
            "symbols": ["CRYPTO:BTC/USDT"],
            "published_at": "2026-10-20T12:00:00Z",
            "effective_at": "2026-10-20T12:00:00Z",
        },
        headers=headers,
    )
    assert duplicate.status_code >= 400


def test_parse_rss_and_auto_ingestion_dedupe(context, monkeypatch):
    from datetime import datetime, timedelta

    from quant.news.classifier import classify
    from quant.news.factors import compute_factor
    from quant.news.ingest import NewsIngestionService, RawNews, parse_rss

    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><title>比特币 ETF 获批</title><link>https://example.com/a</link>
      <guid>a1</guid><pubDate>Wed, 21 Oct 2026 10:00:00 GMT</pubDate>
      <description>监管批准比特币现货 ETF</description></item>
    </channel></rss>"""
    parsed = parse_rss(xml)
    assert parsed[0].title == "比特币 ETF 获批"
    assert parsed[0].url == "https://example.com/a"
    assert parsed[0].published_at.year == 2026

    scored = classify("比特币 ETF 获批，监管批准", force=True)
    assert scored["sentiment"] > 0
    assert "CRYPTO:BTC/USDT" in scored["symbols"]

    now = datetime.utcnow()
    fake = [
        RawNews(
            external_id="auto-1",
            title="比特币 ETF 获批",
            summary="监管批准比特币现货 ETF",
            url="https://example.com/a",
            published_at=now - timedelta(hours=1),
        )
    ]
    monkeypatch.setattr("quant.news.ingest.fetch_feed", lambda *args, **kwargs: fake)
    context.settings.news_feeds = [
        {
            "name": "fake-crypto",
            "kind": "rss",
            "url": "https://example.invalid/rss",
            "market": "CRYPTO",
            "force": True,
        }
    ]
    service = NewsIngestionService(context)
    first = service.run_once()
    assert first["inserted"] == 1
    second = service.run_once()
    assert second["inserted"] == 0
    assert second["skipped"] >= 1

    # 自动抓取的 effective_at 是抓取时刻：抓取之前的 as_of 看不到，之后能看到
    with context.db.session() as session:
        from quant.storage.repositories.news import NewsRepository

        rows = NewsRepository(session).list(symbol="CRYPTO:BTC/USDT", limit=10)
        assert len(rows) == 1
        item = rows[0].to_dict()
    before = compute_factor([item], symbol="CRYPTO:BTC/USDT", as_of=now - timedelta(days=1), window="1d")
    after = compute_factor([item], symbol="CRYPTO:BTC/USDT", as_of=datetime.utcnow(), window="1d")
    assert before.event_count == 0
    assert after.event_count == 1
    assert after.sentiment > 0


def test_news_status_api(client):
    from conftest import login_headers

    headers = login_headers(client)
    resp = client.get("/api/news/status", headers=headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["enabled"] is False  # 测试环境关闭自动抓取，避免联网
    assert isinstance(payload["feeds"], list)


def test_custom_strategy_can_use_news_fields(context):
    spec = {
        "version": 1,
        "description": "新闻情绪过滤",
        "category": "trend",
        "entry": [
            {
                "type": "gt",
                "left": {"kind": "field", "field": "news_sentiment"},
                "right": {"kind": "const", "value": 0.5},
            }
        ],
        "exit": [
            {
                "type": "lt",
                "left": {"kind": "field", "field": "news_sentiment"},
                "right": {"kind": "const", "value": -0.5},
            }
        ],
        "position_pct": 0.5,
        "stop_loss": 0.0,
        "take_profit": 0.0,
    }
    cfg = EngineConfig(
        strategy_id="custom_rule",
        symbols=["CN:600519"],
        start=date(2023, 1, 1),
        end=date(2024, 6, 30),
        initial_cash=1_000_000,
        params={"spec": spec},
        run_id="run_custom_news_pos",
    )
    positive = NewsFactor(
        symbol="CN:600519",
        as_of=datetime(2023, 1, 3),
        window="1h",
        sentiment=0.8,
        policy_risk=0.0,
        confidence=1.0,
        event_count=2,
    )
    result = BacktestEngine(cfg, context.data, news=FakeNews(positive)).run()
    assert result.status == "finished"
    assert result.trades, "新闻情绪为正时应产生买入成交"

    negative = NewsFactor(
        symbol="CN:600519",
        as_of=datetime(2023, 1, 3),
        window="1h",
        sentiment=-0.8,
        policy_risk=0.2,
        confidence=1.0,
        event_count=2,
    )
    cfg.run_id = "run_custom_news_neg"
    blocked = BacktestEngine(cfg, context.data, news=FakeNews(negative)).run()
    assert blocked.status == "finished"
    assert not blocked.trades, "新闻情绪为负时不应入场"


def test_graph_spec_accepts_news_data_node():
    from quant.strategy.custom_graph import parse_graph_spec

    spec = {
        "version": 2,
        "nodes": [
            {"id": "n1", "type": "news_sentiment", "pos": {"x": 0, "y": 0}},
            {"id": "n2", "type": "const", "value": 0.5, "pos": {"x": 0, "y": 100}},
            {"id": "n3", "type": "gt", "pos": {"x": 200, "y": 0}},
        ],
        "edges": [
            {"from": "n1", "to": "n3", "to_port": "left"},
            {"from": "n2", "to": "n3", "to_port": "right"},
        ],
        "outputs": {"entry": "n3", "exit": "n3"},
    }
    parsed = parse_graph_spec(spec)
    assert any(node.type == "news_sentiment" for node in parsed.nodes)
