import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select, update

from binance.exceptions import BinanceAPIException

from backend.exchange import binance_client
from backend.services import telegram_notifier
from backend.core.database import async_session
from backend.core.models import Order, Position
from backend.exchange.symbol_filters import (
    get_max_market_qty, round_price, round_quantity, validate_order,
)

log = structlog.get_logger()

SL_PRICE_OFFSET = Decimal("0.001")
TP_PRICE_OFFSET = Decimal("0.0002")
# Buffer added to SHORT close BUY qty so net received (after commission)
# exceeds the debt.  Prevents Binance AUTO_REPAY 90 % fallback.
SHORT_CLOSE_FEE_BUFFER = Decimal("0.0015")


def _client_order_id(purpose: str, position_id: int) -> str:
    return f"rootcoin_{purpose}_{position_id}_{int(time.time() * 1000)}"


def _close_side(position: Position) -> str:
    return "SELL" if position.side == "LONG" else "BUY"


def _is_margin(position: Position) -> bool:
    return position.market_type != "SPOT"


def _is_isolated(position: Position) -> bool:
    return position.market_type == "ISOLATED_MARGIN"


def _close_qty(pos: Position) -> Decimal:
    """Quantity for fully closing a position.  For SHORT margin, adds a small
    buffer so the BUY commission doesn't make AUTO_REPAY fall back to 90 %."""
    qty = pos.quantity
    if pos.side == "SHORT" and _is_margin(pos):
        qty = qty * (1 + SHORT_CLOSE_FEE_BUFFER)
    return round_quantity(pos.symbol, qty)


async def _save_order(
    binance_order_id: str, symbol: str, side: str, order_type: str,
    status: str, quantity: Decimal, market_type: str, purpose: str,
    position_id: int, price: Decimal | None = None, stop_price: Decimal | None = None,
    order_list_id: str | None = None,
) -> Order:
    order = Order(
        binance_order_id=binance_order_id,
        binance_order_list_id=order_list_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        status=status,
        price=price,
        stop_price=stop_price,
        quantity=quantity,
        market_type=market_type,
        purpose=purpose,
        position_id=position_id,
    )
    async with async_session() as session:
        session.add(order)
        await session.commit()
        await session.refresh(order)
    return order


async def _update_position_order_ref(position: Position, **kwargs):
    async with async_session() as session:
        for k, v in kwargs.items():
            setattr(position, k, v)
        position.updated_at = datetime.now(timezone.utc)
        await session.merge(position)
        await session.commit()


# --- Public API ---


async def place_stop_loss(pos: Position, stop_price: Decimal, qty: Decimal | None = None, *, silent: bool = False) -> dict:
    await cancel_position_orders(pos)
    side = _close_side(pos)
    if qty is not None:
        qty = round_quantity(pos.symbol, qty)
        if pos.side == "SHORT" and _is_margin(pos):
            qty = round_quantity(pos.symbol, qty * (1 + SHORT_CLOSE_FEE_BUFFER))
    else:
        qty = _close_qty(pos)

    if pos.side == "LONG":
        limit_price = round_price(pos.symbol, stop_price * (1 - SL_PRICE_OFFSET))
    else:
        limit_price = round_price(pos.symbol, stop_price * (1 + SL_PRICE_OFFSET))
    stop_price = round_price(pos.symbol, stop_price)

    validate_order(pos.symbol, qty, stop_price)

    kwargs = dict(
        symbol=pos.symbol, side=side, type="STOP_LOSS_LIMIT",
        quantity=str(qty), price=str(limit_price), stopPrice=str(stop_price),
        timeInForce="GTC", newClientOrderId=_client_order_id("sl", pos.id),
    )

    if _is_margin(pos):
        if _is_isolated(pos):
            kwargs["isIsolated"] = "TRUE"
        kwargs["sideEffectType"] = "AUTO_REPAY"
        result = await binance_client.place_margin_order(**kwargs)
    else:
        result = await binance_client.place_order(**kwargs)

    order_id = str(result["orderId"])
    await _save_order(
        binance_order_id=order_id, symbol=pos.symbol, side=side,
        order_type="STOP_LOSS_LIMIT", status="NEW", quantity=qty,
        market_type=pos.market_type, purpose="SL", position_id=pos.id,
        price=limit_price, stop_price=stop_price,
    )
    await _update_position_order_ref(pos, sl_order_id=order_id)

    log.info("sl_placed", symbol=pos.symbol, stop_price=str(stop_price), order_id=order_id)
    if not silent:
        asyncio.create_task(telegram_notifier.notify_sl_placed(
            pos.symbol, pos.side, stop_price, qty, pos.entry_price,
        ))
    return result


async def place_take_profit(pos: Position, tp_price: Decimal) -> dict:
    await cancel_position_orders(pos)
    side = _close_side(pos)
    qty = _close_qty(pos)

    if pos.side == "LONG":
        limit_price = round_price(pos.symbol, tp_price * (1 - TP_PRICE_OFFSET))
    else:
        limit_price = round_price(pos.symbol, tp_price * (1 + TP_PRICE_OFFSET))
    tp_price = round_price(pos.symbol, tp_price)

    validate_order(pos.symbol, qty, tp_price)

    kwargs = dict(
        symbol=pos.symbol, side=side, type="TAKE_PROFIT_LIMIT",
        quantity=str(qty), price=str(limit_price), stopPrice=str(tp_price),
        timeInForce="GTC", newClientOrderId=_client_order_id("tp", pos.id),
    )

    if _is_margin(pos):
        if _is_isolated(pos):
            kwargs["isIsolated"] = "TRUE"
        kwargs["sideEffectType"] = "AUTO_REPAY"
        result = await binance_client.place_margin_order(**kwargs)
    else:
        result = await binance_client.place_order(**kwargs)

    order_id = str(result["orderId"])
    await _save_order(
        binance_order_id=order_id, symbol=pos.symbol, side=side,
        order_type="TAKE_PROFIT_LIMIT", status="NEW", quantity=qty,
        market_type=pos.market_type, purpose="TP", position_id=pos.id,
        price=limit_price, stop_price=tp_price,
    )
    await _update_position_order_ref(pos, tp_order_id=order_id)

    log.info("tp_placed", symbol=pos.symbol, tp_price=str(tp_price), order_id=order_id)
    asyncio.create_task(telegram_notifier.notify_tp_placed(
        pos.symbol, pos.side, tp_price, qty, pos.entry_price,
    ))
    return result


async def place_oco(pos: Position, tp_price: Decimal, sl_price: Decimal, *, silent: bool = False) -> dict:
    await cancel_position_orders(pos)
    side = _close_side(pos)
    qty = _close_qty(pos)
    tp_price = round_price(pos.symbol, tp_price)
    sl_price = round_price(pos.symbol, sl_price)

    if pos.side == "LONG":
        sl_limit = round_price(pos.symbol, sl_price * (1 - SL_PRICE_OFFSET))
    else:
        sl_limit = round_price(pos.symbol, sl_price * (1 + SL_PRICE_OFFSET))

    # Margin LIMIT_MAKER: no TP offset needed (order sits on book at exact price)
    # Spot TAKE_PROFIT_LIMIT: small offset between trigger and limit for fill safety
    if _is_margin(pos):
        tp_limit = tp_price
    elif pos.side == "LONG":
        tp_limit = round_price(pos.symbol, tp_price * (1 - TP_PRICE_OFFSET))
    else:
        tp_limit = round_price(pos.symbol, tp_price * (1 + TP_PRICE_OFFSET))

    validate_order(pos.symbol, qty, sl_price)

    current = pos.current_price
    if current and current > 0:
        if pos.side == "LONG":
            if not (sl_price < current < tp_limit):
                raise ValueError(
                    f"Prix invalide : SL ({sl_price}) < prix actuel ({current}) < TP ({tp_limit}) requis. "
                    f"Le prix actuel est hors de la fourchette SL/TP."
                )
        else:
            if not (tp_limit < current < sl_price):
                raise ValueError(
                    f"Prix invalide : TP ({tp_limit}) < prix actuel ({current}) < SL ({sl_price}) requis. "
                    f"Le prix actuel est hors de la fourchette SL/TP."
                )

    if _is_margin(pos):
        # Margin OCO uses old format: price (LIMIT_MAKER/TP) + stopPrice/stopLimitPrice (SL)
        kwargs = dict(
            symbol=pos.symbol, side=side, quantity=str(qty),
            price=str(tp_limit),
            stopPrice=str(sl_price),
            stopLimitPrice=str(sl_limit),
            stopLimitTimeInForce="GTC",
        )
        if _is_isolated(pos):
            kwargs["isIsolated"] = "TRUE"
        kwargs["sideEffectType"] = "AUTO_REPAY"
        result = await binance_client.place_margin_oco_order(**kwargs)
    else:
        # Spot OCO uses new format: above/below
        if pos.side == "LONG":
            kwargs = dict(
                symbol=pos.symbol, side=side, quantity=str(qty),
                aboveType="TAKE_PROFIT_LIMIT", abovePrice=str(tp_limit),
                aboveStopPrice=str(tp_price), aboveTimeInForce="GTC",
                belowType="STOP_LOSS_LIMIT", belowPrice=str(sl_limit),
                belowStopPrice=str(sl_price), belowTimeInForce="GTC",
            )
        else:
            kwargs = dict(
                symbol=pos.symbol, side=side, quantity=str(qty),
                aboveType="STOP_LOSS_LIMIT", abovePrice=str(sl_limit),
                aboveStopPrice=str(sl_price), aboveTimeInForce="GTC",
                belowType="TAKE_PROFIT_LIMIT", belowPrice=str(tp_limit),
                belowStopPrice=str(tp_price), belowTimeInForce="GTC",
            )
        result = await binance_client.place_oco_order(**kwargs)

    order_list_id = str(result.get("orderListId", ""))
    await _save_order(
        binance_order_id=None, symbol=pos.symbol, side=side,
        order_type="OCO", status="NEW", quantity=qty,
        market_type=pos.market_type, purpose="OCO", position_id=pos.id,
        price=tp_price, stop_price=sl_price, order_list_id=order_list_id,
    )
    await _update_position_order_ref(
        pos, sl_order_id=None, tp_order_id=None, oco_order_list_id=order_list_id,
    )

    log.info("oco_placed", symbol=pos.symbol, tp=str(tp_price), sl=str(sl_price))
    if not silent:
        asyncio.create_task(telegram_notifier.notify_oco_placed(
            pos.symbol, pos.side, tp_price, sl_price, qty, pos.entry_price,
        ))
    return result


def _build_chunks(symbol: str, total_qty: Decimal, max_qty: Decimal) -> list[Decimal]:
    chunks: list[Decimal] = []
    remaining = total_qty
    while remaining > 0:
        chunk = round_quantity(symbol, min(remaining, max_qty))
        if chunk <= 0:
            break
        chunks.append(chunk)
        remaining = round_quantity(symbol, remaining - chunk)
    return chunks


# Binance error codes related to quantity limits
_QTY_ERROR_CODES = {-1013, -3084, -2010}


async def _place_single_market(
    pos: Position, place_fn, side: str, chunk_qty: Decimal,
    id_tag: str, db_purpose: str,
) -> tuple[dict, str]:
    """Place one MARKET order and save to DB."""
    kwargs = dict(
        symbol=pos.symbol, side=side, type="MARKET",
        quantity=str(chunk_qty),
        newClientOrderId=_client_order_id(id_tag, pos.id),
    )
    if _is_margin(pos):
        if _is_isolated(pos):
            kwargs["isIsolated"] = "TRUE"
        kwargs["sideEffectType"] = "AUTO_REPAY"
    result = await place_fn(**kwargs)
    order_id = str(result["orderId"])
    await _save_order(
        binance_order_id=order_id, symbol=pos.symbol, side=side,
        order_type="MARKET", status="NEW", quantity=chunk_qty,
        market_type=pos.market_type, purpose=db_purpose, position_id=pos.id,
    )
    return result, order_id


_MAX_RECHUNK_ATTEMPTS = 4  # /10 each time → max division by 10_000


async def _place_market_chunked(
    pos: Position, side: str, total_qty: Decimal,
    id_tag: str, db_purpose: str,
) -> tuple[dict, str]:
    """Place MARKET order(s), splitting into chunks if qty exceeds MARKET_LOT_SIZE.
    Auto-retries with progressively smaller chunks if Binance rejects for quantity."""
    place_fn = binance_client.place_margin_order if _is_margin(pos) else binance_client.place_order

    max_market = get_max_market_qty(pos.symbol)
    if max_market and total_qty > max_market:
        chunks = _build_chunks(pos.symbol, total_qty, max_market)
        log.info("market_order_chunked", symbol=pos.symbol, total=str(total_qty),
                 chunks=len(chunks), max_market=str(max_market))
    else:
        chunks = [total_qty]

    # Try placing the first chunk — if qty rejected, halve and retry
    for attempt in range(_MAX_RECHUNK_ATTEMPTS + 1):
        try:
            last_result, last_order_id = await _place_single_market(
                pos, place_fn, side, chunks[0], id_tag, db_purpose,
            )
            break
        except BinanceAPIException as exc:
            if exc.code not in _QTY_ERROR_CODES or attempt == _MAX_RECHUNK_ATTEMPTS:
                raise
            # Shrink chunks: divide max by 10 each attempt
            divisor = 10 ** (attempt + 1)
            fallback_max = round_quantity(pos.symbol, total_qty / divisor)
            if fallback_max <= 0:
                raise
            chunks = _build_chunks(pos.symbol, total_qty, fallback_max)
            log.warning("market_order_rechunk", symbol=pos.symbol, attempt=attempt + 1,
                        fallback_max=str(fallback_max), chunks=len(chunks),
                        error=exc.message)

    # Place remaining chunks (first one already done)
    for chunk_qty in chunks[1:]:
        last_result, last_order_id = await _place_single_market(
            pos, place_fn, side, chunk_qty, id_tag, db_purpose,
        )

    return last_result, last_order_id


async def close_position(pos: Position, pct: int = 100) -> dict:
    is_full = pct >= 100
    if is_full:
        await cancel_position_orders(pos)
        qty = _close_qty(pos)
    else:
        partial = pos.quantity * Decimal(pct) / Decimal(100)
        if pos.side == "SHORT" and _is_margin(pos):
            partial = partial * (1 + SHORT_CLOSE_FEE_BUFFER)
        qty = round_quantity(pos.symbol, partial)

    side = _close_side(pos)
    id_tag = "close" if is_full else f"pclose{pct}"

    result, order_id = await _place_market_chunked(pos, side, qty, id_tag, "CLOSE")

    log.info("close_placed", symbol=pos.symbol, side=side, qty=str(qty),
             pct=pct, order_id=order_id)
    return result


SECURE_FEE_BUFFER = Decimal("0.002")  # 0.2% above breakeven to cover fees


async def secure_position(pos: Position) -> dict:
    """Sell half at market, cancel existing orders, place SL at breakeven +0.2%."""
    full_qty = pos.quantity
    half_qty = round_quantity(pos.symbol, full_qty / 2)
    remaining_qty = round_quantity(pos.symbol, full_qty - half_qty)

    current_price = pos.current_price or Decimal("0")
    if current_price <= 0:
        raise ValueError("Prix actuel indisponible")

    if pos.side == "LONG":
        sl_price = round_price(pos.symbol, pos.entry_price * (1 + SECURE_FEE_BUFFER))
    else:
        sl_price = round_price(pos.symbol, pos.entry_price * (1 - SECURE_FEE_BUFFER))

    if pos.side == "LONG" and current_price <= sl_price:
        raise ValueError(
            f"Position pas assez en profit. Prix actuel ({current_price}) "
            f"doit etre > SL breakeven ({sl_price})"
        )
    if pos.side == "SHORT" and current_price >= sl_price:
        raise ValueError(
            f"Position pas assez en profit. Prix actuel ({current_price}) "
            f"doit etre < SL breakeven ({sl_price})"
        )

    validate_order(pos.symbol, half_qty, current_price)
    validate_order(pos.symbol, remaining_qty, sl_price)

    # Step 1: cancel existing SL/TP/OCO to free locked funds
    try:
        await cancel_position_orders(pos)
    except Exception:
        log.warning("secure_cancel_orders_failed", symbol=pos.symbol, exc_info=True)

    # Step 2: market sell/buy half (chunked if exceeds MARKET_LOT_SIZE)
    side = _close_side(pos)
    market_result, market_order_id = await _place_market_chunked(
        pos, side, half_qty, "secure", "SECURE_SELL",
    )
    log.info("secure_half_sold", symbol=pos.symbol, qty=str(half_qty),
             order_id=market_order_id)

    # Step 3: SL at breakeven +0.2% for remaining half
    sl_result = await place_stop_loss(pos, sl_price, qty=remaining_qty, silent=True)

    log.info("position_secured", symbol=pos.symbol, half_sold=str(half_qty),
             sl_price=str(sl_price), remaining=str(remaining_qty))
    asyncio.create_task(telegram_notifier.notify_position_secured(
        pos.symbol, pos.side, half_qty, sl_price, remaining_qty,
    ))

    return {
        "market_order_id": market_order_id,
        "sl_order_id": str(sl_result["orderId"]),
        "half_qty": str(half_qty),
        "remaining_qty": str(remaining_qty),
        "sl_price": str(sl_price),
    }


async def cancel_order(order_db_id: int) -> dict:
    async with async_session() as session:
        order = await session.get(Order, order_db_id)
        if not order:
            raise ValueError(f"Order {order_db_id} not found")

    result = await cancel_order_by_binance_id(
        order.symbol, order.binance_order_id, order.market_type,
    )

    order.status = "CANCELED"
    order.updated_at = datetime.now(timezone.utc)
    async with async_session() as session:
        await session.merge(order)
        await session.commit()

    # Clean position refs in DB (in-memory refs updated by WS execution_report)
    if order.position_id:
        async with async_session() as session:
            pos = await session.get(Position, order.position_id)
            if pos:
                changed = False
                if pos.sl_order_id == order.binance_order_id:
                    pos.sl_order_id = None
                    changed = True
                if pos.tp_order_id == order.binance_order_id:
                    pos.tp_order_id = None
                    changed = True
                if changed:
                    pos.updated_at = datetime.now(timezone.utc)
                    await session.commit()

    log.info("order_cancelled", order_id=order.binance_order_id, symbol=order.symbol)
    return result


async def cancel_position_orders(pos: Position) -> dict:
    cancelled = 0
    updates = {}

    if pos.oco_order_list_id:
        try:
            await _cancel_oco(pos.symbol, pos.oco_order_list_id, pos.market_type)
            cancelled += 1
        except Exception:
            log.warning("oco_cancel_failed_direct", symbol=pos.symbol,
                        order_list_id=pos.oco_order_list_id, exc_info=True)
            # Fallback: query open orders to find the real OCO list ID
            try:
                real_oco_id = await _find_oco_list_id_by_symbol(
                    pos.symbol, pos.market_type)
                if real_oco_id:
                    await _cancel_oco(pos.symbol, real_oco_id, pos.market_type)
                    cancelled += 1
            except Exception:
                log.error("oco_cancel_fallback_failed", symbol=pos.symbol, exc_info=True)
        updates["oco_order_list_id"] = None
        updates["sl_order_id"] = None
        updates["tp_order_id"] = None
    else:
        oco_cancelled = False
        for order_id in [pos.sl_order_id, pos.tp_order_id]:
            if not order_id:
                continue
            try:
                await cancel_order_by_binance_id(pos.symbol, order_id, pos.market_type)
                cancelled += 1
            except Exception:
                if not oco_cancelled:
                    oco_id = await _find_oco_list_id(pos.symbol, order_id, pos.market_type)
                    if oco_id:
                        try:
                            await _cancel_oco(pos.symbol, oco_id, pos.market_type)
                            oco_cancelled = True
                            cancelled += 1
                        except Exception:
                            log.error("oco_cancel_by_order_failed", symbol=pos.symbol,
                                      order_id=order_id, oco_id=oco_id, exc_info=True)
        if pos.sl_order_id:
            updates["sl_order_id"] = None
        if pos.tp_order_id:
            updates["tp_order_id"] = None

    if updates:
        await _update_position_order_ref(pos, **updates)

    # Mark all NEW order records in DB as CANCELED so fetch_order_prices
    # never picks up stale prices when a new OCO is placed right after.
    async with async_session() as session:
        await session.execute(
            update(Order)
            .where(Order.position_id == pos.id, Order.status == "NEW")
            .values(status="CANCELED", updated_at=datetime.now(timezone.utc))
        )
        await session.commit()

    log.info("position_orders_cancelled", symbol=pos.symbol, position_id=pos.id, cancelled=cancelled)
    return {"cancelled": cancelled}


async def cleanup_stale_orders(position_id: int, open_order_ids: set[str]):
    async with async_session() as session:
        rows = (await session.execute(
            select(Order).where(
                Order.position_id == position_id,
                Order.status == "NEW",
                Order.binance_order_id.isnot(None),
            )
        )).scalars().all()
        changed = 0
        for order in rows:
            if order.binance_order_id not in open_order_ids:
                order.status = "CANCELED"
                order.updated_at = datetime.now(timezone.utc)
                changed += 1
        if changed:
            await session.commit()
            log.info("stale_orders_cleaned", position_id=position_id, count=changed)


async def mark_order_status(binance_order_id: str, status: str):
    async with async_session() as session:
        order = (await session.execute(
            select(Order).where(Order.binance_order_id == binance_order_id)
        )).scalar_one_or_none()
        if order and order.status == "NEW":
            order.status = status
            order.updated_at = datetime.now(timezone.utc)
            await session.commit()


async def mark_oco_done(order_list_id: str):
    async with async_session() as session:
        order = (await session.execute(
            select(Order).where(
                Order.binance_order_list_id == order_list_id,
                Order.purpose == "OCO",
                Order.status == "NEW",
            )
        )).scalar_one_or_none()
        if order:
            order.status = "CANCELED"
            order.updated_at = datetime.now(timezone.utc)
            await session.commit()


async def ensure_oco_order_record(pos: Position, oco_list_id: str, open_orders: list):
    async with async_session() as session:
        existing = (await session.execute(
            select(Order).where(
                Order.binance_order_list_id == oco_list_id,
                Order.purpose == "OCO",
            )
        )).scalar_one_or_none()
        if existing:
            return

    close_side = "SELL" if pos.side == "LONG" else "BUY"
    tp_price = sl_price = None
    for o in open_orders:
        if str(o.get("orderListId", "")) != oco_list_id:
            continue
        otype = o.get("type", "")
        if otype == "LIMIT_MAKER":
            tp_price = Decimal(str(o.get("price", "0")))
        elif otype == "STOP_LOSS_LIMIT":
            sl_price = Decimal(str(o.get("stopPrice", "0")))

    if tp_price and sl_price:
        order = Order(
            binance_order_id=None,
            binance_order_list_id=oco_list_id,
            symbol=pos.symbol,
            side=close_side,
            order_type="OCO",
            status="NEW",
            price=tp_price,
            stop_price=sl_price,
            quantity=pos.quantity,
            market_type=pos.market_type,
            purpose="OCO",
            position_id=pos.id,
        )
        async with async_session() as session:
            session.add(order)
            await session.commit()
        log.info("oco_order_record_created", symbol=pos.symbol, order_list_id=oco_list_id,
                 tp=str(tp_price), sl=str(sl_price))


async def ensure_order_record(pos: Position, binance_order: dict, purpose: str):
    oid = str(binance_order.get("orderId", ""))
    if not oid:
        return
    async with async_session() as session:
        existing = (await session.execute(
            select(Order).where(Order.binance_order_id == oid)
        )).scalar_one_or_none()
        if existing:
            return
    otype = binance_order.get("type", "")
    stop_price = Decimal(str(binance_order.get("stopPrice", "0"))) or None
    price = Decimal(str(binance_order.get("price", "0"))) or None
    qty = Decimal(str(binance_order.get("origQty", "0")))
    side = binance_order.get("side", "")
    order_list_id = str(binance_order.get("orderListId", ""))
    if order_list_id in ("", "-1"):
        order_list_id = None
    order = Order(
        binance_order_id=oid,
        binance_order_list_id=order_list_id,
        symbol=pos.symbol,
        side=side,
        order_type=otype,
        status="NEW",
        price=price,
        stop_price=stop_price,
        quantity=qty,
        market_type=pos.market_type,
        purpose=purpose,
        position_id=pos.id,
    )
    async with async_session() as session:
        session.add(order)
        await session.commit()
    log.info("order_record_created", symbol=pos.symbol, order_id=oid, purpose=purpose)


async def cancel_order_by_binance_id(symbol: str, order_id: str, market_type: str) -> dict:
    if market_type == "SPOT":
        return await binance_client.cancel_order(symbol, order_id)
    else:
        return await binance_client.cancel_margin_order(
            symbol, order_id, is_isolated=(market_type == "ISOLATED_MARGIN"),
        )


async def _find_oco_list_id(symbol: str, order_id: str, market_type: str) -> str | None:
    try:
        open_orders = await _get_open_orders(symbol, market_type)
        for o in open_orders:
            if str(o.get("orderId", "")) == order_id:
                list_id = str(o.get("orderListId", ""))
                if list_id not in ("", "-1"):
                    return list_id
    except Exception:
        pass
    return None


async def _find_oco_list_id_by_symbol(symbol: str, market_type: str) -> str | None:
    try:
        open_orders = await _get_open_orders(symbol, market_type)
        for o in open_orders:
            list_id = str(o.get("orderListId", ""))
            if list_id not in ("", "-1"):
                return list_id
    except Exception:
        pass
    return None


async def _get_open_orders(symbol: str, market_type: str) -> list:
    if market_type == "SPOT":
        return await binance_client.get_open_orders(symbol)
    return await binance_client.get_margin_open_orders(
        symbol, is_isolated=(market_type == "ISOLATED_MARGIN"),
    )


async def _cancel_oco(symbol: str, order_list_id: str, market_type: str):
    if market_type == "SPOT":
        await binance_client.cancel_oco_order(symbol, order_list_id)
    else:
        await binance_client.cancel_margin_oco_order(
            symbol, order_list_id,
            is_isolated=(market_type == "ISOLATED_MARGIN"),
        )
    await mark_oco_done(order_list_id)
