"""自选应用服务：规范化、去重、上限校验。"""

from __future__ import annotations

from typing import Any

from ..core.errors import InvalidRequest
from ..core.markets import try_parse
from ..storage.repositories.watchlist import WatchlistRepository

MAX_WATCHLIST = 30


def _normalize(symbols: list[str]) -> list[str]:
    out: list[str] = []
    for raw in symbols:
        text = str(raw).strip()
        if not text:
            continue
        parsed = try_parse(text)
        value = parsed.id if parsed is not None else text.upper()
        if value not in out:
            out.append(value)
    return out


def list_items(ctx: Any, *, user_id: int) -> list[str]:
    with ctx.db.session() as session:
        return WatchlistRepository(session).list_for(user_id)


def replace(ctx: Any, *, user_id: int, symbols: list[str]) -> list[str]:
    values = _normalize(symbols)
    if len(values) > MAX_WATCHLIST:
        raise InvalidRequest(f"自选最多 {MAX_WATCHLIST} 个")
    with ctx.db.session() as session:
        return WatchlistRepository(session).replace(user_id, values)


__all__ = ["MAX_WATCHLIST", "list_items", "replace"]
