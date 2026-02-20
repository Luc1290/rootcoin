from fastapi import APIRouter, Query
from sqlalchemy import select, func

from backend.database import async_session
from backend.models import Balance

router = APIRouter(prefix="/api/balances", tags=["balances"])


@router.get("")
async def get_current_balances():
    async with async_session() as session:
        # Get the latest snapshot_at
        latest = await session.execute(select(func.max(Balance.snapshot_at)))
        max_ts = latest.scalar()
        if not max_ts:
            return []
        result = await session.execute(
            select(Balance).where(Balance.snapshot_at == max_ts)
        )
        return [
            {
                "asset": b.asset,
                "free": str(b.free),
                "locked": str(b.locked),
                "borrowed": str(b.borrowed),
                "interest": str(b.interest),
                "net": str(b.net),
                "wallet_type": b.wallet_type,
                "usd_value": str(b.usd_value) if b.usd_value else None,
                "snapshot_at": b.snapshot_at.isoformat(),
            }
            for b in result.scalars().all()
        ]


@router.get("/history")
async def get_balance_history(asset: str | None = Query(None), limit: int = Query(100, le=1000)):
    async with async_session() as session:
        q = select(Balance).order_by(Balance.snapshot_at.desc()).limit(limit)
        if asset:
            q = q.where(Balance.asset == asset)
        result = await session.execute(q)
        return [
            {
                "asset": b.asset,
                "net": str(b.net),
                "wallet_type": b.wallet_type,
                "snapshot_at": b.snapshot_at.isoformat(),
            }
            for b in result.scalars().all()
        ]
