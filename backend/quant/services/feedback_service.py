"""反馈管理服务（管理员）。"""

from __future__ import annotations

from typing import Any

from ..core.errors import NotFoundError
from ..storage.repositories.feedback import FeedbackRepository


def list_items(ctx: Any, *, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with ctx.db.session() as session:
        return [record.to_dict() for record in FeedbackRepository(session).list(status=status, limit=limit)]


def update_status(ctx: Any, *, feedback_id: int, status: str) -> dict[str, Any]:
    with ctx.db.session() as session:
        repo = FeedbackRepository(session)
        record = repo.get(feedback_id)
        if record is None:
            raise NotFoundError("反馈不存在")
        return repo.update_status(record, status).to_dict()


__all__ = ["list_items", "update_status"]
