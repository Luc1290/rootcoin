from binance.exceptions import BinanceAPIException
from fastapi import APIRouter, HTTPException, Query

from backend.exchange import binance_client
from backend.trading import order_manager

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("/open")
async def get_open_orders(symbol: str = Query(None)):
    try:
        orders = await binance_client.get_margin_open_orders(symbol=symbol)
        return [
            {
                "orderId": o["orderId"],
                "orderListId": o.get("orderListId", -1),
                "symbol": o["symbol"],
                "side": o["side"],
                "type": o["type"],
                "price": o.get("price", "0"),
                "stopPrice": o.get("stopPrice", "0"),
                "origQty": o.get("origQty", "0"),
                "executedQty": o.get("executedQty", "0"),
                "status": o["status"],
                "time": o.get("time"),
            }
            for o in orders
        ]
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.delete("/{order_id}")
async def cancel_order(order_id: int):
    try:
        result = await order_manager.cancel_order(order_id)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")
