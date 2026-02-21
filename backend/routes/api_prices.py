from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import select

from backend.database import async_session
from backend.models import Price
from backend import position_tracker

router = APIRouter(prefix="/api/prices", tags=["prices"])


@router.get("/{symbol}")
async def get_price_history(
    symbol: str,
    limit: int = Query(1000, le=5000),
    hours: int | None = Query(None, le=720),
    order: str = Query("desc"),
):
    async with async_session() as session:
        q = select(Price).where(Price.symbol == symbol)
        if hours:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            q = q.where(Price.recorded_at >= cutoff)
        if order == "asc":
            q = q.order_by(Price.recorded_at.asc())
        else:
            q = q.order_by(Price.recorded_at.desc())
        q = q.limit(limit)
        result = await session.execute(q)
        return [
            {
                "price": str(p.price),
                "recorded_at": p.recorded_at.isoformat(),
            }
            for p in result.scalars().all()
        ]


@router.get("/{symbol}/current")
async def get_current_price(symbol: str):
    for pos in position_tracker.get_positions():
        if pos.symbol == symbol and pos.current_price:
            return {"symbol": symbol, "price": str(pos.current_price)}

    # Fallback: last recorded price from DB
    async with async_session() as session:
        result = await session.execute(
            select(Price)
            .where(Price.symbol == symbol)
            .order_by(Price.recorded_at.desc())
            .limit(1)
        )
        p = result.scalar_one_or_none()
        if p:
            return {"symbol": symbol, "price": str(p.price)}
    return {"symbol": symbol, "price": None}
