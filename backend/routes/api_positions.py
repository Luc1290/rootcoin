from decimal import Decimal

from binance.exceptions import BinanceAPIException
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend import order_manager, position_tracker
from backend.database import async_session
from backend.models import Order

router = APIRouter(prefix="/api/positions", tags=["positions"])


class PriceBody(BaseModel):
    price: str


class OcoBody(BaseModel):
    tp_price: str
    sl_price: str


async def _fetch_order_prices(pos_ids: list[int]) -> dict:
    if not pos_ids:
        return {}
    async with async_session() as session:
        rows = (await session.execute(
            select(Order.position_id, Order.purpose, Order.stop_price)
            .where(Order.position_id.in_(pos_ids), Order.status == "NEW",
                   Order.purpose.in_(["SL", "TP"]))
        )).all()
    result = {}
    for pid, purpose, stop_price in rows:
        entry = result.setdefault(pid, {})
        key = "sl_price" if purpose == "SL" else "tp_price"
        entry[key] = str(stop_price) if stop_price else None
    return result


def _pos_to_dict(pos, order_prices=None) -> dict:
    from datetime import datetime, timezone

    duration = ""
    if pos.opened_at:
        opened = pos.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - opened
        hours, rem = divmod(int(delta.total_seconds()), 3600)
        minutes = rem // 60
        if hours > 24:
            days = hours // 24
            duration = f"{days}d {hours % 24}h"
        else:
            duration = f"{hours}h {minutes}m"

    current = pos.current_price or Decimal("0")
    qty = pos.quantity or Decimal("0")
    entry_fees = pos.entry_fees_usd or Decimal("0")
    exit_fees_est = qty * current * Decimal("0.001")
    prices = (order_prices or {}).get(pos.id, {})

    return {
        "id": pos.id,
        "symbol": pos.symbol,
        "side": pos.side,
        "entry_price": str(pos.entry_price) if pos.entry_price else "0",
        "current_price": str(current),
        "quantity": str(qty),
        "pnl_usd": str(pos.pnl_usd) if pos.pnl_usd else "0",
        "pnl_pct": str(pos.pnl_pct) if pos.pnl_pct else "0",
        "entry_fees_usd": str(entry_fees),
        "exit_fees_est": str(exit_fees_est),
        "market_type": pos.market_type,
        "sl_order_id": pos.sl_order_id,
        "tp_order_id": pos.tp_order_id,
        "oco_order_list_id": pos.oco_order_list_id,
        "sl_price": prices.get("sl_price"),
        "tp_price": prices.get("tp_price"),
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        "duration": duration,
    }


@router.get("")
async def list_positions():
    positions = position_tracker.get_positions()
    pos_ids = [p.id for p in positions if p.sl_order_id or p.tp_order_id or p.oco_order_list_id]
    order_prices = await _fetch_order_prices(pos_ids)
    return [_pos_to_dict(p, order_prices) for p in positions]


@router.get("/{position_id}")
async def get_position(position_id: int):
    for p in position_tracker.get_positions():
        if p.id == position_id:
            order_prices = await _fetch_order_prices([p.id])
            return _pos_to_dict(p, order_prices)
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
