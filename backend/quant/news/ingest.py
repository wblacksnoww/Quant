"""自动抓取新闻：RSS/Atom + 华尔街见闻 JSON → 去重 → 规则打分 → news_items。"""

from __future__ import annotations

import hashlib
import html
import logging
import os
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

import httpx
from sqlalchemy.exc import IntegrityError

from ..storage.models import utcnow_naive
from ..storage.repositories.news import NewsRepository
from .classifier import classify
from .feeds import DEFAULT_FEEDS

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


@dataclass
class RawNews:
    external_id: str
    title: str
    summary: str
    url: str
    published_at: datetime


def _clean(text: str | None, limit: int = 2000) -> str:
    if not text:
        return ""
    value = html.unescape(_TAG_RE.sub(" ", text))
    return _SPACE_RE.sub(" ", value).strip()[:limit]


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        parsed = parsedate_to_datetime(text)
        return _naive_utc(parsed)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return _naive_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ElementTree.Element, names: Iterable[str]) -> str:
    for child in node:
        if _local(child.tag) in names:
            return "".join(child.itertext())
    return ""


def _atom_link(node: ElementTree.Element) -> str:
    for child in node:
        if _local(child.tag) == "link":
            href = child.attrib.get("href")
            if href:
                return href
            if child.text:
                return child.text.strip()
    return ""


def parse_rss(payload: bytes | str, *, limit: int = 100) -> list[RawNews]:
    """解析 RSS 2.0 / Atom；解析失败抛 ValueError。"""
    root = ElementTree.fromstring(payload)
    nodes: list[ElementTree.Element] = []
    for child in root.iter():
        if _local(child.tag) in ("item", "entry"):
            nodes.append(child)
    out: list[RawNews] = []
    for node in nodes[:limit]:
        title = _clean(_child_text(node, ("title",)), 300)
        summary = _clean(
            _child_text(node, ("description", "summary", "content", "encoded")),
            2000,
        )
        link = _atom_link(node) or _clean(_child_text(node, ("link", "guid", "id")), 600)
        if not title and not summary:
            continue
        raw_id = _child_text(node, ("guid", "id")) or link or f"{title}|{summary[:80]}"
        published = (
            _parse_date(_child_text(node, ("pubdate", "published", "updated", "date"))) or utcnow_naive()
        )
        out.append(
            RawNews(
                external_id=str(raw_id)[:160],
                title=title or summary[:120],
                summary=summary,
                url=link,
                published_at=published,
            )
        )
    return out


def parse_wallstreetcn(payload: dict, *, limit: int = 100) -> list[RawNews]:
    items = ((payload.get("data") or {}).get("items")) or []
    out: list[RawNews] = []
    for item in items[:limit]:
        title = _clean(item.get("title") or "", 300)
        summary = _clean(item.get("content_text") or item.get("content") or "", 2000)
        if not title and not summary:
            continue
        ts = item.get("display_time")
        try:
            published = datetime.utcfromtimestamp(float(ts)) if ts else utcnow_naive()
        except (TypeError, ValueError, OSError):
            published = utcnow_naive()
        out.append(
            RawNews(
                external_id=str(
                    item.get("id") or hashlib.sha256(f"{title}|{summary[:80]}".encode()).hexdigest()
                )[:160],
                title=title or summary[:120],
                summary=summary,
                url=str(item.get("uri") or ""),
                published_at=published,
            )
        )
    return out


def fetch_feed(feed: dict[str, Any], *, proxy: str = "", timeout: float = 12.0) -> list[RawNews]:
    kind = str(feed.get("kind") or "rss").lower()
    url = str(feed.get("url") or "").strip()
    if not url:
        raise ValueError("新闻源缺少 url")
    limit = int(feed.get("limit") or 50)
    headers = {"User-Agent": "quant-trading-news/1.0 (+https://github.com/wblacksnoww/Quant)"}
    kwargs: dict[str, Any] = {"headers": headers, "timeout": timeout, "follow_redirects": True}
    if proxy:
        kwargs["proxy"] = proxy
    with httpx.Client(**kwargs) as client:
        response = client.get(url)
        response.raise_for_status()
        if len(response.content) > 4_000_000:
            raise ValueError("新闻源响应过大")
        if kind == "wallstreetcn":
            return parse_wallstreetcn(response.json(), limit=limit)
        return parse_rss(response.content, limit=limit)


class NewsIngestionService:
    """后台定时抓取 + 手动触发；单进程内串行执行，避免重复抓取。"""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.feeds: list[dict[str, Any]] = [
            feed for feed in (ctx.settings.news_feeds or DEFAULT_FEEDS) if feed.get("enabled", True)
        ]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_lock = threading.Lock()
        self._trigger_thread: threading.Thread | None = None
        self._last_result: dict[str, Any] | None = None
        self._last_at: str | None = None

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if not self.ctx.settings.news_auto_fetch or not self.feeds:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="news-ingestion", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None
        if self._trigger_thread is not None:
            self._trigger_thread.join(timeout=timeout)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.ctx.settings.news_auto_fetch),
            "interval_seconds": float(self.ctx.settings.news_fetch_interval),
            "feeds": [str(feed.get("name") or feed.get("url")) for feed in self.feeds],
            "last_run_at": self._last_at,
            "last_result": self._last_result,
        }

    def trigger(self) -> bool:
        """手动触发一次抓取（后台线程，立即返回）。"""
        if self._run_lock.locked():
            return False
        if self._trigger_thread is not None and self._trigger_thread.is_alive():
            return False
        self._trigger_thread = threading.Thread(
            target=self.run_once, name="news-ingestion-manual", daemon=True
        )
        self._trigger_thread.start()
        return True

    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        interval = max(30.0, float(self.ctx.settings.news_fetch_interval))
        # 启动后先抓一次，再按间隔轮询
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("自动抓取新闻失败")
            self._stop.wait(interval)

    def run_once(self) -> dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return self._last_result or {"feeds": [], "inserted": 0, "skipped": 0, "busy": True}
        result: dict[str, Any] = {
            "started_at": utcnow_naive().isoformat(),
            "feeds": [],
            "inserted": 0,
            "skipped": 0,
            "errors": 0,
        }
        try:
            for feed in self.feeds:
                name = str(feed.get("name") or feed.get("url"))
                row: dict[str, Any] = {"name": name, "fetched": 0, "inserted": 0, "skipped": 0}
                try:
                    items = fetch_feed(
                        feed,
                        proxy=str(
                            self.ctx.settings.news_proxy or os.environ.get("QUANT_CRYPTO_PROXY", "") or ""
                        ),
                        timeout=float(feed.get("timeout") or 12.0),
                    )
                    row["fetched"] = len(items)
                    inserted, skipped = self._store(feed, items)
                    row["inserted"] = inserted
                    row["skipped"] = skipped
                    result["inserted"] += inserted
                    result["skipped"] += skipped
                except Exception as exc:  # noqa: BLE001 - 单源失败不影响其他源
                    row["error"] = f"{type(exc).__name__}: {exc}"
                    result["errors"] += 1
                    log.warning("新闻源 %s 抓取失败: %s", name, exc)
                result["feeds"].append(row)
        finally:
            result["finished_at"] = utcnow_naive().isoformat()
            self._last_result = result
            self._last_at = result["finished_at"]
            self._run_lock.release()
        return result

    def _store(self, feed: dict[str, Any], items: list[RawNews]) -> tuple[int, int]:
        inserted = skipped = 0
        now = utcnow_naive()
        cutoff = now - timedelta(days=7)
        keywords = [str(item).lower() for item in (feed.get("keywords") or [])]
        for raw in items:
            text = f"{raw.title} {raw.summary}".strip()
            if keywords and not any(word in text.lower() for word in keywords):
                skipped += 1
                continue
            if raw.published_at < cutoff or raw.published_at > now + timedelta(hours=2):
                skipped += 1
                continue
            classified = classify(
                text,
                default_market=str(feed.get("market") or ""),
                default_symbols=feed.get("symbols") or (),
                force=bool(feed.get("force")),
            )
            if not classified["relevant"]:
                skipped += 1
                continue
            if not classified["symbols"] and not classified["market"] and not feed.get("force"):
                skipped += 1
                continue
            try:
                with self.ctx.db.session() as session:
                    NewsRepository(session).create(
                        title=raw.title,
                        summary=raw.summary,
                        url=raw.url,
                        source=str(feed.get("name") or "auto"),
                        external_id=raw.external_id,
                        published_at=raw.published_at,
                        effective_at=now,  # 自动抓取：从抓取时刻起才可见，避免回测未来函数
                        symbols=classified["symbols"],
                        market=classified["market"],
                        event_type=classified["event_type"],
                        sentiment=classified["sentiment"],
                        policy_risk=classified["policy_risk"],
                        confidence=classified["confidence"],
                        created_by=None,
                    )
                inserted += 1
            except IntegrityError:
                skipped += 1
            except Exception:  # noqa: BLE001
                log.exception("写入新闻失败: %s", raw.title[:80])
                skipped += 1
        return inserted, skipped


__all__ = [
    "NewsIngestionService",
    "RawNews",
    "fetch_feed",
    "parse_rss",
    "parse_wallstreetcn",
]
