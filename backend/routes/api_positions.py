from decimal import Decimal

from binance.exceptions import BinanceAPIException
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import order_manager, position_tracker

router = APIRouter(prefix="/api/positions", tags=["positions"])


class PriceBody(BaseModel):
    price: str


class OcoBody(BaseModel):
    tp_price: str
    sl_price: str


def _pos_to_dict(pos) -> dict:
    return {
        "id": pos.id,
        "symbol": pos.symbol,
        "side": pos.side,
        "entry_price": str(pos.entry_price),
        "quantity": str(pos.quantity),
        "market_type": pos.market_type,
        "current_price": str(pos.current_price) if pos.current_price else None,
        "pnl_usd": str(pos.pnl_usd) if pos.pnl_usd else None,
        "pnl_pct": str(pos.pnl_pct) if pos.pnl_pct else None,
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        "sl_order_id": pos.sl_order_id,
        "tp_order_id": pos.tp_order_id,
        "oco_order_list_id": pos.oco_order_list_id,
    }


@router.get("")
async def list_positions():
    return [_pos_to_dict(p) for p in position_tracker.get_positions()]


@router.get("/{position_id}")
async def get_position(position_id: int):
    for p in position_tracker.get_positions():
        if p.id == position_id:
            return _pos_to_dict(p)
    raise HTTPException(404, "Position not found")


@router.post("/{position_id}/sl")
async def set_stop_loss(position_id: int, body: PriceBody):
    try:
        result = await order_manager.place_stop_loss(position_id, Decimal(body.price))
        return {"status": "ok", "order_id": str(result["orderId"])}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.post("/{position_id}/tp")
async def set_take_profit(position_id: int, body: PriceBody):
    try:
        result = await order_manager.place_take_profit(position_id, Decimal(body.price))
        return {"status": "ok", "order_id": str(result["orderId"])}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.post("/{position_id}/oco")
async def set_oco(position_id: int, body: OcoBody):
    try:
        result = await order_manager.place_oco(
            position_id, Decimal(body.tp_price), Decimal(body.sl_price),
        )
        return {"status": "ok", "order_list_id": str(result.get("orderListId", ""))}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.post("/{position_id}/cancel-orders")
async def cancel_orders(position_id: int):
    try:
        result = await order_manager.cancel_position_orders(position_id)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.post("/{position_id}/close")
async def close_position(position_id: int):
    try:
        result = await order_manager.close_position(position_id)
        return {"status": "ok", "order_id": str(result["orderId"])}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")
