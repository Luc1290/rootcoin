import time
from decimal import Decimal, InvalidOperation

from binance.exceptions import BinanceAPIException
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.exchange import binance_client
from backend.exchange.symbol_filters import round_price, round_quantity, validate_order
from backend.trading import order_manager, position_tracker
from backend.routes.position_helpers import fetch_order_prices, pos_to_dict

router = APIRouter(prefix="/api/positions", tags=["positions"])

OPEN_LEVERAGE = Decimal("5")
OPEN_SAFETY = Decimal("0.98")


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


class CloseBody(BaseModel):
    pct: int = 100


class OpenBody(BaseModel):
    symbol: str
    side: str  # LONG or SHORT
    price: str | None = None  # None = MARKET


@router.get("")
async def list_positions():
    positions = position_tracker.get_positions()
    pos_ids = [p.id for p in positions if p.sl_order_id or p.tp_order_id or p.oco_order_list_id]
    order_prices = await fetch_order_prices(pos_ids)
    return [pos_to_dict(p, order_prices) for p in positions]


@router.get("/open/preview")
async def open_preview(symbol: str = Query(...)):
    symbol = symbol.upper()
    try:
        client = await binance_client.get_client()
        cross_assets = await binance_client.get_cross_margin_balances()
        usdc_free = Decimal("0")
        for a in cross_assets:
            if a["asset"] == "USDC":
                usdc_free = Decimal(a["free"])
                break

        ticker = await client.get_symbol_ticker(symbol=symbol)
        current_price = Decimal(ticker["price"])

        notional = usdc_free * OPEN_LEVERAGE * OPEN_SAFETY
        max_qty = round_quantity(symbol, notional / current_price)

        return {
            "symbol": symbol,
            "usdc_free": str(usdc_free),
            "current_price": str(current_price),
            "max_qty": str(max_qty),
            "notional": str(round(notional, 2)),
            "leverage": str(OPEN_LEVERAGE),
        }
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/open")
async def open_position(body: OpenBody):
    symbol = body.symbol.upper()
    side = body.side.upper()
    if side not in ("LONG", "SHORT"):
        raise HTTPException(400, "side must be LONG or SHORT")

    try:
        client = await binance_client.get_client()

        cross_assets = await binance_client.get_cross_margin_balances()
        usdc_free = Decimal("0")
        for a in cross_assets:
            if a["asset"] == "USDC":
                usdc_free = Decimal(a["free"])
                break

        if body.price:
            price = Decimal(body.price)
        else:
            ticker = await client.get_symbol_ticker(symbol=symbol)
            price = Decimal(ticker["price"])

        notional = usdc_free * OPEN_LEVERAGE * OPEN_SAFETY
        qty = round_quantity(symbol, notional / price)
        if body.price:
            price = round_price(symbol, price)

        validate_order(symbol, qty, price)

        order_side = "BUY" if side == "LONG" else "SELL"
        cid = f"rootcoin_open_{int(time.time() * 1000)}"

        kwargs = dict(
            symbol=symbol,
            side=order_side,
            quantity=str(qty),
            sideEffectType="MARGIN_BUY",
            newClientOrderId=cid,
        )

        if body.price:
            kwargs["type"] = "LIMIT"
            kwargs["price"] = str(price)
            kwargs["timeInForce"] = "GTC"
        else:
            kwargs["type"] = "MARKET"

        result = await binance_client.place_margin_order(**kwargs)

        return {
            "status": "ok",
            "order_id": str(result["orderId"]),
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "price": str(price),
            "type": kwargs["type"],
        }
    except (ValueError, InvalidOperation) as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


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
    except (ValueError, InvalidOperation) as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.post("/{position_id}/tp")
async def set_take_profit(position_id: int, body: PriceBody):
    try:
        pos = _find_position(position_id)
        result = await order_manager.place_take_profit(pos, Decimal(body.price))
        return {"status": "ok", "order_id": str(result["orderId"])}
    except (ValueError, InvalidOperation) as e:
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
    except (ValueError, InvalidOperation) as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.post("/{position_id}/cancel-orders")
async def cancel_orders(position_id: int):
    try:
        pos = _find_position(position_id)
        result = await order_manager.cancel_position_orders(pos)
        return result
    except (ValueError, InvalidOperation) as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.post("/{position_id}/close")
async def close_position(position_id: int, body: CloseBody = CloseBody()):
    try:
        pct = max(1, min(body.pct, 100))
        pos = _find_position(position_id)
        result = await order_manager.close_position(pos, pct=pct)
        return {"status": "ok", "order_id": str(result["orderId"])}
    except (ValueError, InvalidOperation) as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")


@router.post("/{position_id}/secure")
async def secure_position(position_id: int):
    try:
        pos = _find_position(position_id)
        result = await order_manager.secure_position(pos)
        return {"status": "ok", **result}
    except (ValueError, InvalidOperation) as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")
