from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from backend.core.database import async_session
from backend.core.models import Balance

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

CHANGE_THRESHOLD = Decimal("0.1")  # filter out sub-$0.1 noise


def _compress(rows, threshold=CHANGE_THRESHOLD):
    """Keep only points where total_usd changed significantly."""
    if len(rows) <= 2:
        return rows
    out = [rows[0]]
    last_val = rows[0].total_usd or Decimal("0")
    for row in rows[1:-1]:
        val = row.total_usd or Decimal("0")
        if abs(val - last_val) >= threshold:
            out.append(row)
            last_val = val
    out.append(rows[-1])
    return out


@router.get("/history")
async def get_portfolio_history(
    hours: int = Query(24, le=720),
    limit: int = Query(500, le=2000),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    async with async_session() as session:
        result = await session.execute(
            select(
                Balance.snapshot_at,
                func.sum(Balance.usd_value).label("total_usd"),
            )
            .where(Balance.snapshot_at >= cutoff, Balance.usd_value.isnot(None))
            .group_by(Balance.snapshot_at)
            .order_by(Balance.snapshot_at.asc())
        )
        rows = result.all()

    rows = _compress(rows)

    if len(rows) > limit:
        step = len(rows) / limit
        sampled = [rows[int(i * step)] for i in range(limit - 1)]
        sampled.append(rows[-1])
        rows = sampled

    return [
        {
            "total_usd": str(row.total_usd),
            "snapshot_at": row.snapshot_at.isoformat(),
        }
        for row in rows
    ]
