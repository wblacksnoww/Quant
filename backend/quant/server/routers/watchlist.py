"""账号级自选路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...services import watchlist_service
from ...storage.models import User
from ..context import AppContext
from ..deps import get_ctx, get_current_user
from ..schemas import WatchlistUpdate

router = APIRouter(prefix="/watchlist", tags=["自选"])


@router.get("", response_model=list[str], summary="我的自选")
def list_watchlist(
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
):
    return watchlist_service.list_items(ctx, user_id=user.id)


@router.put("", response_model=list[str], summary="覆盖保存自选（最多 30 个）")
def replace_watchlist(
    body: WatchlistUpdate,
    ctx: AppContext = Depends(get_ctx),
    user: User = Depends(get_current_user),
):
    return watchlist_service.replace(ctx, user_id=user.id, symbols=body.symbols)


__all__ = ["router"]
