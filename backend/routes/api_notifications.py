from fastapi import APIRouter, Query

from backend.services import notification_logger

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
async def get_notifications(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    type: str | None = Query(None),
    symbol: str | None = Query(None),
):
    return {
        "notifications": await notification_logger.get_history(
            limit=limit, offset=offset,
            type_filter=type, symbol=symbol,
        )
    }


@router.delete("")
async def clear_notifications():
    count = await notification_logger.clear_all()
    return {"deleted": count}


@router.get("/stats")
async def get_stats():
    return await notification_logger.get_stats()
