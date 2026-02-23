from decimal import Decimal

from binance.exceptions import BinanceAPIException
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import order_manager, position_tracker
from backend.routes.position_helpers import fetch_order_prices, pos_to_dict

router = APIRouter(prefix="/api/positions", tags=["positions"])


def _find_position(position_id: int):
    for p in position_tracker.get_positions():
        if p.id == position_id:
            return p
    raise HTTPException(404, "Position not found or inactive")


class PriceBody(BaseModel):
    price: str


class OcoBody(BaseModel):
    tp_price: str
    sl_price: str


@router.get("")
async def list_positions():
    positions = position_tracker.get_positions()
    pos_ids = [p.id for p in positions if p.sl_order_id or p.tp_order_id or p.oco_order_list_id]
    order_prices = await fetch_order_prices(pos_ids)
    return [pos_to_dict(p, order_prices) for p in positions]


@router.get("/{position_id}")
async def get_position(position_id: int):
    for p in position_tracker.get_positions():
        if p.id == position_id:
            order_prices = await fetch_order_prices([p.id])
            return pos_to_dict(p, order_prices)
    raise HTTPException(404, "Position not found")


@router.post("/{position_id}/sl")
async def set_stop_loss(position_id: int, body: PriceBody):
    try:
        pos = _find_position(position_id)
        result = await order_manager.place_stop_loss(pos, Decimal(body.price))
        return {"status": "ok", "order_id": str(result["orderId"])}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.post("/{position_id}/tp")
async def set_take_profit(position_id: int, body: PriceBody):
    try:
        pos = _find_position(position_id)
        result = await order_manager.place_take_profit(pos, Decimal(body.price))
        return {"status": "ok", "order_id": str(result["orderId"])}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.post("/{position_id}/oco")
async def set_oco(position_id: int, body: OcoBody):
    try:
        pos = _find_position(position_id)
        result = await order_manager.place_oco(
            pos, Decimal(body.tp_price), Decimal(body.sl_price),
        )
        return {"status": "ok", "order_list_id": str(result.get("orderListId", ""))}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.post("/{position_id}/cancel-orders")
async def cancel_orders(position_id: int):
    try:
        pos = _find_position(position_id)
        result = await order_manager.cancel_position_orders(pos)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.post("/{position_id}/close")
async def close_position(position_id: int):
    try:
        pos = _find_position(position_id)
        result = await order_manager.close_position(pos)
        return {"status": "ok", "order_id": str(result["orderId"])}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")
