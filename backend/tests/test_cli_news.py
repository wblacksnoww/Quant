"""CLI 新闻因子重算：词表调整后必须能把旧条目拉齐，且可重复执行。"""

from __future__ import annotations

import base64
import secrets
from datetime import datetime

import pytest
from sqlalchemy import select

from quant import cli
from quant.config import Settings, reset_settings
from quant.storage.db import Database
from quant.storage.models import NewsItem
from quant.storage.repositories import NewsRepository


@pytest.fixture()
def env(tmp_path, monkeypatch):
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    monkeypatch.setenv("QUANT_MASTER_KEY", key)
    monkeypatch.setenv("QUANT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("QUANT_DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    reset_settings()
    yield Settings(
        env="test",
        database_url=f"sqlite:///{tmp_path}/cli.db",
        data_dir=str(tmp_path),
        master_key=key,
    )
    reset_settings()


def _seed(url: str) -> None:
    """造三条旧分数明显不对的条目（模拟旧版词表打出来的数据）。"""
    db = Database(url)
    db.create_all()
    stamp = datetime(2026, 11, 5, 9, 0)
    samples = [
        ("Live updates: Bitcoin drops more than 5% as stocks close lower", 0.0, 0.0, 0.35),
        ("提醒：日内请重点关注（以下均为北京时间）", 0.2, 0.85, 0.95),
        ("美联储宣布降息50个基点", 0.25, 0.65, 0.55),
    ]
    with db.session() as session:
        repo = NewsRepository(session)
        for title, sent, risk, conf in samples:
            repo.create(
                source="manual",
                title=title,
                summary="",
                url="",
                published_at=stamp,
                effective_at=stamp,
                symbols=["ALL"],
                market="",
                event_type="other",
                sentiment=sent,
                policy_risk=risk,
                confidence=conf,
            )
    db.dispose()


def _rows(url: str) -> list[NewsItem]:
    db = Database(url)
    with db.session() as session:
        rows = list(session.scalars(select(NewsItem).order_by(NewsItem.id)))
    db.dispose()
    return rows


def test_rescore_fixes_stale_scores(env):
    _seed(env.database_url)
    assert cli.main(["news", "rescore"]) == 0

    rows = _rows(env.database_url)
    assert len(rows) == 3
    # 英文下跌不再是 0
    assert rows[0].sentiment < -0.3
    # 日历提醒不该再有全库最高置信度
    assert rows[1].confidence < 0.35
    # 降息是政策明朗，不该被当成政策风险
    assert rows[2].policy_risk < 0.3
    assert rows[2].sentiment > 0.5


def test_rescore_is_idempotent(env):
    _seed(env.database_url)
    assert cli.main(["news", "rescore"]) == 0
    first = [(row.sentiment, row.policy_risk, row.confidence) for row in _rows(env.database_url)]
    assert cli.main(["news", "rescore"]) == 0
    second = [(row.sentiment, row.policy_risk, row.confidence) for row in _rows(env.database_url)]
    assert first == second


def test_dry_run_does_not_write(env):
    _seed(env.database_url)
    before = [(row.sentiment, row.policy_risk, row.confidence) for row in _rows(env.database_url)]
    assert cli.main(["news", "rescore", "--dry-run"]) == 0
    after = [(row.sentiment, row.policy_risk, row.confidence) for row in _rows(env.database_url)]
    assert before == after


def test_unknown_action_is_rejected_by_parser(env):
    with pytest.raises(SystemExit):
        cli.main(["news", "bogus"])
