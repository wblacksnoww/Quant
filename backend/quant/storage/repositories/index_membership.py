"""指数成分股的历史区间查询与快照导入。

只支持一种数据来源：**快照**。真实世界里几乎拿不到"官方逐日成分变动"的免费
接口，能拿到的是"今天这个指数包含哪些股票"。所以这里的做法是：把每次拿到的
快照导进来，用相邻快照的差集反推每只股票的进出日期。

    2023-01-01 快照：[A, B, C]
    2023-07-01 快照：[A, C, D]

    →  A: 2023-01-01 ~ 至今
        B: 2023-01-01 ~ 2023-06-30   （掉出）
        C: 2023-01-01 ~ 至今
        D: 2023-07-01 ~ 至今          （新进）

快照越密，区间越准。这是一个**近似**：真实的调仓发生在某一天，而两次快照之间
的变动都被归到后一次快照的日期上。查询结果里会带 ``point_in_time`` 标记，
区间不完整时必须让调用方知道。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import IndexMembership

__all__ = ["IndexMembershipRepository"]


class IndexMembershipRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    def members_at(self, index_code: str, as_of: date) -> list[str]:
        """``as_of`` 当天在指数内的标的。

        起止日期都是闭区间；``start_date`` / ``end_date`` 为空视为"未知"，
        分别当作"一直存在"和"至今仍在"处理。
        """
        stmt = (
            select(IndexMembership.symbol)
            .where(IndexMembership.index_code == index_code)
            .where((IndexMembership.start_date.is_(None)) | (IndexMembership.start_date <= as_of))
            .where((IndexMembership.end_date.is_(None)) | (IndexMembership.end_date >= as_of))
            .order_by(IndexMembership.symbol)
        )
        return [row[0] for row in self.session.execute(stmt).all()]

    def has_history(self, index_code: str) -> bool:
        """是否导入过这个指数的任何成分数据。"""
        stmt = select(IndexMembership.id).where(IndexMembership.index_code == index_code).limit(1)
        return self.session.execute(stmt).first() is not None

    def list_indexes(self) -> list[dict[str, object]]:
        """所有已导入的指数概览。"""
        rows = self.session.execute(select(IndexMembership)).scalars().all()
        grouped: dict[str, dict[str, object]] = {}
        for row in rows:
            entry = grouped.setdefault(
                row.index_code,
                {
                    "index_code": row.index_code,
                    "entries": 0,
                    "active": 0,
                    "earliest": None,
                    "latest": None,
                    "sources": set(),
                },
            )
            entry["entries"] = int(entry["entries"]) + 1  # type: ignore[arg-type]
            if row.end_date is None:
                entry["active"] = int(entry["active"]) + 1  # type: ignore[arg-type]
            if row.start_date is not None:
                current = entry["earliest"]
                if current is None or row.start_date < current:  # type: ignore[operator]
                    entry["earliest"] = row.start_date
            sources = entry["sources"]
            if isinstance(sources, set):
                sources.add(row.source)
        for entry in grouped.values():
            sources = entry.pop("sources", set())
            entry["sources"] = sorted(sources)  # type: ignore[arg-type]
            earliest = entry["earliest"]
            entry["earliest"] = earliest.isoformat() if isinstance(earliest, date) else None
        return sorted(grouped.values(), key=lambda item: str(item["index_code"]))

    def timeline(self, index_code: str) -> list[dict[str, object]]:
        stmt = (
            select(IndexMembership)
            .where(IndexMembership.index_code == index_code)
            .order_by(IndexMembership.start_date, IndexMembership.symbol)
        )
        return [row.to_dict() for row in self.session.execute(stmt).scalars().all()]

    # ------------------------------------------------------------------ #
    def replace_snapshot(
        self,
        index_code: str,
        symbols: Sequence[str],
        as_of: date,
        *,
        source: str = "snapshot",
    ) -> dict[str, int]:
        """导入一份 ``as_of`` 当天的成分快照。

        掉出名单的标的补 ``end_date = as_of - 1 天``（前一天还是成分股），
        新进的从 ``as_of`` 开始。重复导入同一份快照是幂等的。
        """
        wanted = {str(item).strip() for item in symbols if str(item).strip()}
        if not wanted:
            raise ValueError("快照为空：至少要有一个标的")

        current = {
            row.symbol: row
            for row in self.session.execute(
                select(IndexMembership)
                .where(IndexMembership.index_code == index_code)
                .where(IndexMembership.end_date.is_(None))
            )
            .scalars()
            .all()
        }

        added = removed = kept = 0
        for symbol in wanted:
            if symbol in current:
                kept += 1
                continue
            self.session.add(
                IndexMembership(
                    index_code=index_code,
                    symbol=symbol,
                    start_date=as_of,
                    end_date=None,
                    source=source,
                )
            )
            added += 1

        leaving = as_of - timedelta(days=1)
        for symbol, row in current.items():
            if symbol in wanted:
                continue
            # 快照日期早于该标的的起始日（乱序导入）时不改，避免生成
            # start > end 的非法区间
            if row.start_date is not None and row.start_date > leaving:
                continue
            row.end_date = leaving
            removed += 1

        self.session.flush()
        return {"added": added, "removed": removed, "kept": kept}
