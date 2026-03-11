import asyncio
import time
from decimal import Decimal, InvalidOperation

import structlog
from binance.exceptions import BinanceAPIException
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.exchange import binance_client
from backend.exchange.symbol_filters import round_price, round_quantity, validate_order
from backend.trading import order_manager, position_tracker, trailing_manager

from backend.routes.position_helpers import fetch_order_prices, pos_to_dict

log = structlog.get_logger()

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
    amount_usdc: str | None = None  # None = full balance
    account_type: str = "MARGIN"  # SPOT or MARGIN


@router.get("")
async def list_positions():
    positions = position_tracker.get_positions()
    pos_ids = [p.id for p in positions if p.sl_order_id or p.tp_order_id or p.oco_order_list_id]
    order_prices = await fetch_order_prices(pos_ids)
    return [pos_to_dict(p, order_prices) for p in positions]


@router.get("/open/preview")
async def open_preview(
    symbol: str = Query(...),
    account_type: str = Query("MARGIN"),
):
    symbol = symbol.upper()
    account_type = account_type.upper()

    try:
        client = await binance_client.get_client()
        ticker = await client.get_symbol_ticker(symbol=symbol)
        current_price = Decimal(ticker["price"])

        if account_type == "SPOT":
            spot_balances = await binance_client.get_spot_balances()
            usdc_free = Decimal("0")
            for a in spot_balances:
                if a["asset"] == "USDC":
                    usdc_free = Decimal(a["free"])
                    break

            notional = usdc_free * OPEN_SAFETY
            max_qty = round_quantity(symbol, notional / current_price)

            return {
                "symbol": symbol,
                "usdc_free": str(usdc_free),
                "current_price": str(current_price),
                "max_qty": str(max_qty),
                "notional": str(round(notional, 2)),
                "leverage": "1",
                "account_type": "SPOT",
            }

        # MARGIN (default)
        cross_assets = await binance_client.get_cross_margin_balances()
        usdc_free = Decimal("0")
        for a in cross_assets:
            if a["asset"] == "USDC":
                usdc_free = Decimal(a["free"])
                break

        naive_notional = usdc_free * OPEN_LEVERAGE * OPEN_SAFETY

        base_asset = symbol.replace("USDC", "").replace("USDT", "")
        max_borrow_usdc = Decimal("0")
        max_borrow_base = Decimal("0")
        try:
            info_usdc = await client.get_max_margin_loan(asset="USDC")
            max_borrow_usdc = Decimal(str(info_usdc.get("amount", "0")))
        except Exception:
            pass
        try:
            info_base = await client.get_max_margin_loan(asset=base_asset)
            max_borrow_base = Decimal(str(info_base.get("amount", "0")))
        except Exception:
            pass

        long_notional = min(naive_notional, (usdc_free + max_borrow_usdc) * OPEN_SAFETY)
        short_notional = min(naive_notional, max_borrow_base * current_price * OPEN_SAFETY)
        notional = max(long_notional, short_notional)
        max_qty = round_quantity(symbol, notional / current_price)

        return {
            "symbol": symbol,
            "usdc_free": str(usdc_free),
            "current_price": str(current_price),
            "max_qty": str(max_qty),
            "notional": str(round(notional, 2)),
            "leverage": str(OPEN_LEVERAGE),
            "account_type": "MARGIN",
        }
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/open")
async def open_position(body: OpenBody):
    symbol = body.symbol.upper()
    side = body.side.upper()
    account_type = (body.account_type or "MARGIN").upper()

    if side not in ("LONG", "SHORT"):
        raise HTTPException(400, "side must be LONG or SHORT")
    if account_type == "SPOT" and side == "SHORT":
        raise HTTPException(400, "SHORT non disponible en spot")

    try:
        client = await binance_client.get_client()

        if body.price:
            price = Decimal(body.price)
        else:
            ticker = await client.get_symbol_ticker(symbol=symbol)
            price = Decimal(ticker["price"])

        # ── SPOT ──────────────────────────────────────────────
        if account_type == "SPOT":
            spot_balances = await binance_client.get_spot_balances()
            usdc_free = Decimal("0")
            for a in spot_balances:
                if a["asset"] == "USDC":
                    usdc_free = Decimal(a["free"])
                    break

            if body.amount_usdc:
                user_amount = Decimal(body.amount_usdc)
                if user_amount <= 0:
                    raise HTTPException(400, "amount_usdc must be > 0")
                if user_amount > usdc_free:
                    raise HTTPException(400, f"Montant ({user_amount}) > USDC spot ({usdc_free})")
                notional = user_amount * OPEN_SAFETY
            else:
                notional = usdc_free * OPEN_SAFETY

            qty = round_quantity(symbol, notional / price)
            if body.price:
                price = round_price(symbol, price)
            validate_order(symbol, qty, price)

            cid = f"rootcoin_spot_{int(time.time() * 1000)}"
            kwargs = dict(
                symbol=symbol,
                side="BUY",
                quantity=str(qty),
                newClientOrderId=cid,
            )
            if body.price:
                kwargs["type"] = "LIMIT"
                kwargs["price"] = str(price)
                kwargs["timeInForce"] = "GTC"
            else:
                kwargs["type"] = "MARKET"

            log.info("open_spot", symbol=symbol, notional=str(notional), qty=str(qty))
            result = await binance_client.place_order(**kwargs)

            return {
                "status": "ok",
                "order_id": str(result["orderId"]),
                "symbol": symbol,
                "side": "LONG",
                "qty": str(qty),
                "price": str(price),
                "type": kwargs["type"],
                "account_type": "SPOT",
            }

        # ── MARGIN (existing logic) ──────────────────────────
        cross_assets = await binance_client.get_cross_margin_balances()
        usdc_free = Decimal("0")
        for a in cross_assets:
            if a["asset"] == "USDC":
                usdc_free = Decimal(a["free"])
                break

        base_asset = symbol.replace("USDC", "").replace("USDT", "")
        debt_asset = base_asset if side == "SHORT" else "USDC"
        for a in cross_assets:
            if a["asset"] == debt_asset:
                borrowed = Decimal(a.get("borrowed", "0"))
                free = Decimal(a.get("free", "0"))
                if borrowed > 0:
                    repay_amount = min(free, borrowed)
                    if repay_amount > 0:
                        try:
                            await binance_client.repay_margin_loan(
                                asset=debt_asset, amount=repay_amount,
                            )
                            log.info("open_pre_repay", asset=debt_asset,
                                     repaid=str(repay_amount))
                        except Exception:
                            log.warning("open_pre_repay_failed", asset=debt_asset,
                                        amount=str(repay_amount), exc_info=True)
                    remaining_debt = borrowed - repay_amount
                    if remaining_debt > 0:
                        raise HTTPException(
                            400,
                            f"Dette {debt_asset} résiduelle: {remaining_debt} "
                            f"(borrowed={borrowed}, free={free}). "
                            f"Rembourser manuellement sur Binance avant d'ouvrir.",
                        )
                    if debt_asset == "USDC" and repay_amount > 0:
                        usdc_free = free - repay_amount
                break

        borrow_asset = base_asset if side == "SHORT" else "USDC"
        max_borrow_info = await client.get_max_margin_loan(asset=borrow_asset)
        max_borrow = Decimal(str(max_borrow_info.get("amount", "0")))

        if body.amount_usdc:
            user_amount = Decimal(body.amount_usdc)
            if user_amount <= 0:
                raise HTTPException(400, "amount_usdc must be > 0")
            if user_amount > usdc_free:
                raise HTTPException(400, f"Montant demandé ({user_amount}) > USDC dispo ({usdc_free})")
            naive_notional = user_amount * OPEN_LEVERAGE * OPEN_SAFETY
        else:
            naive_notional = usdc_free * OPEN_LEVERAGE * OPEN_SAFETY

        if side == "SHORT":
            max_borrow_notional = max_borrow * price * OPEN_SAFETY
        else:
            max_borrow_notional = (usdc_free + max_borrow) * OPEN_SAFETY

        notional = min(naive_notional, max_borrow_notional)

        log.info("open_position_calc", symbol=symbol, side=side,
                 usdc_free=str(usdc_free), max_borrow=str(max_borrow),
                 notional=str(notional))

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
            "account_type": "MARGIN",
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


@router.post("/{position_id}/force-close")
async def force_close_position(position_id: int):
    """Mark position as closed in DB without placing any Binance order.
    Useful for residual balances too small to sell."""
    pos = _find_position(position_id)
    await position_tracker.force_close(pos)
    return {"status": "ok", "closed_id": position_id}


@router.post("/{position_id}/secure")
async def secure_position(position_id: int):
    try:
        pos = _find_position(position_id)
        result = await order_manager.secure_position(pos)
        asyncio.create_task(trailing_manager.resume_after_secure(position_id))
        return {"status": "ok", **result}
    except (ValueError, InvalidOperation) as e:
        raise HTTPException(400, str(e))
    except BinanceAPIException as e:
        raise HTTPException(400, f"Binance: {e.message}")
