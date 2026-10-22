"""用户反馈：普通用户只提交，列表和状态管理仅管理员可用。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...storage.models import User
from ...storage.repositories import FeedbackRepository
from ..deps import audit, get_current_user, get_db
from ..schemas import FeedbackIn, Message

router = APIRouter(prefix="/feedback", tags=["反馈"])


@router.post("", response_model=Message, status_code=201, summary="提交意见/建议")
def create_feedback(
    body: FeedbackIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    record = FeedbackRepository(session).create(
        user_id=user.id,
        username=user.username,
        category=body.category,
        content=body.content,
    )
    audit(
        session,
        request=None,
        actor=user.username,
        action="feedback.create",
        target=f"feedback:{record.id}",
        detail={"category": record.category},
    )
    return Message(message="反馈已提交，感谢你的建议", detail={"id": record.id})


__all__ = ["router"]
