from fastapi import APIRouter, Query
from sqlalchemy import select

from backend.database import async_session
from backend.models import Trade

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("")
async def get_trades(symbol: str | None = Query(None), limit: int = Query(100, le=1000)):
    async with async_session() as session:
        q = select(Trade).order_by(Trade.executed_at.desc()).limit(limit)
        if symbol:
            q = q.where(Trade.symbol == symbol)
        result = await session.execute(q)
        return [
            {
                "id": t.id,
                "binance_trade_id": t.binance_trade_id,
                "symbol": t.symbol,
                "side": t.side,
                "price": str(t.price),
                "quantity": str(t.quantity),
                "quote_qty": str(t.quote_qty) if t.quote_qty else None,
                "commission": str(t.commission) if t.commission else None,
                "commission_asset": t.commission_asset,
                "market_type": t.market_type,
                "is_maker": t.is_maker,
                "executed_at": t.executed_at.isoformat(),
            }
            for t in result.scalars().all()
        ]
