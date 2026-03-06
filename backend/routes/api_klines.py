from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from backend.market import kline_manager
from backend.trading import position_tracker
from backend.exchange import ws_manager
from backend.core.database import async_session
from backend.core.models import Trade

router = APIRouter(prefix="/api/klines", tags=["klines"])


@router.get("/symbols")
async def get_symbols():
    base = ["BTCUSDC", "ETHUSDC"]
    pos_symbols = list({p.symbol for p in position_tracker.get_positions()})
    return list(dict.fromkeys(base + pos_symbols))


@router.get("/{symbol}")
async def get_klines(
    symbol: str,
    interval: str = Query("1h"),
    limit: int = Query(500, le=1500),
    indicators: str = Query("ma,volume"),
):
    if interval not in kline_manager.VALID_INTERVALS:
        raise HTTPException(400, f"Invalid interval: {interval}")

    await kline_manager.fetch_and_store(symbol, interval, limit=limit)
    klines = await kline_manager.get_klines(symbol, interval, limit)

    requested = {i.strip() for i in indicators.split(",") if i.strip()}
    computed = kline_manager.compute_indicators(klines, requested) if klines else {}

    return {
        "symbol": symbol,
        "interval": interval,
        "klines": klines,
        "indicators": computed,
    }


@router.get("/{symbol}/trades")
async def get_trades_for_chart(
    symbol: str,
    start_time: str | None = Query(None),
    end_time: str | None = Query(None),
):
    async with async_session() as session:
        q = select(Trade).where(Trade.symbol == symbol).order_by(Trade.executed_at.asc())
        if start_time:
            q = q.where(Trade.executed_at >= datetime.fromisoformat(start_time))
        if end_time:
            q = q.where(Trade.executed_at <= datetime.fromisoformat(end_time))
        q = q.limit(500)
        result = await session.execute(q)
        return [
            {
                "side": t.side,
                "price": str(t.price),
                "quantity": str(t.quantity),
                "executed_at": t.executed_at.isoformat(),
            }
            for t in result.scalars().all()
        ]


@router.post("/{symbol}/subscribe")
async def subscribe_kline_stream(symbol: str, interval: str = Query("1h")):
    if interval not in kline_manager.VALID_INTERVALS:
        raise HTTPException(400, f"Invalid interval: {interval}")
    await ws_manager.subscribe_kline(symbol, interval)
    return {"status": "subscribed", "symbol": symbol, "interval": interval}


@router.post("/{symbol}/unsubscribe")
async def unsubscribe_kline_stream(symbol: str, interval: str = Query("1h")):
    await ws_manager.unsubscribe_kline(symbol, interval)
    return {"status": "unsubscribed", "symbol": symbol, "interval": interval}
