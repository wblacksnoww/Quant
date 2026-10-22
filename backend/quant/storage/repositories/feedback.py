"""用户反馈仓储。"""

from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..models import Feedback


class FeedbackRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, user_id: int | None, username: str, category: str, content: str) -> Feedback:
        record = Feedback(
            user_id=user_id,
            username=username,
            category=category,
            content=content.strip(),
            status="new",
        )
        self.session.add(record)
        self.session.flush()
        return record

    def get(self, feedback_id: int) -> Feedback | None:
        return self.session.get(Feedback, feedback_id)

    def list(self, *, status: str | None = None, limit: int = 200) -> list[Feedback]:
        stmt = select(Feedback).order_by(desc(Feedback.created_at)).limit(limit)
        if status:
            stmt = stmt.where(Feedback.status == status)
        return list(self.session.scalars(stmt))

    def update_status(self, record: Feedback, status: str) -> Feedback:
        record.status = status
        self.session.flush()
        return record


__all__ = ["FeedbackRepository"]
