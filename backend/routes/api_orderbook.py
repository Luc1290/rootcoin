from fastapi import APIRouter, HTTPException

from backend import orderbook_tracker

router = APIRouter(prefix="/api/orderbook", tags=["orderbook"])


@router.get("")
async def get_all_orderbooks():
    return orderbook_tracker.get_orderbook_data()


@router.get("/{symbol}")
async def get_orderbook(symbol: str):
    data = orderbook_tracker.get_orderbook_data(symbol)
    if not data:
        raise HTTPException(404, f"No orderbook data for {symbol}")
    return data
