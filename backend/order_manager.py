import time
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select

from backend import binance_client, position_tracker
from backend.database import async_session
from backend.models import Order, Position
from backend.utils.symbol_filters import round_price, round_quantity, validate_order

log = structlog.get_logger()

SL_PRICE_OFFSET = Decimal("0.001")
TP_PRICE_OFFSET = Decimal("0.001")
# Buffer added to SHORT close BUY qty so net received (after commission)
# exceeds the debt.  Prevents Binance AUTO_REPAY 90 % fallback.
SHORT_CLOSE_FEE_BUFFER = Decimal("0.0015")


def _client_order_id(purpose: str, position_id: int) -> str:
    return f"rootcoin_{purpose}_{position_id}_{int(time.time() * 1000)}"


def _get_position(position_id: int) -> Position:
    for pos in position_tracker.get_positions():
        if pos.id == position_id:
            return pos
    raise ValueError(f"Position {position_id} not found or inactive")


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


async def place_stop_loss(position_id: int, stop_price: Decimal) -> dict:
    pos = _get_position(position_id)
    side = _close_side(pos)
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
    return result


async def place_take_profit(position_id: int, tp_price: Decimal) -> dict:
    pos = _get_position(position_id)
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
    return result


async def place_oco(position_id: int, tp_price: Decimal, sl_price: Decimal) -> dict:
    pos = _get_position(position_id)
    side = _close_side(pos)
    qty = _close_qty(pos)
    tp_price = round_price(pos.symbol, tp_price)
    sl_price = round_price(pos.symbol, sl_price)

    # Cancel existing SL/TP/OCO if any
    if pos.sl_order_id:
        try:
            await cancel_order_by_binance_id(pos.symbol, pos.sl_order_id, pos.market_type)
        except Exception:
            pass
    if pos.tp_order_id:
        try:
            await cancel_order_by_binance_id(pos.symbol, pos.tp_order_id, pos.market_type)
        except Exception:
            pass
    if pos.oco_order_list_id:
        try:
            await _cancel_oco(pos.symbol, pos.oco_order_list_id, pos.market_type)
        except Exception:
            pass

    if pos.side == "LONG":
        sl_limit = round_price(pos.symbol, sl_price * (1 - SL_PRICE_OFFSET))
        tp_limit = round_price(pos.symbol, tp_price * (1 - TP_PRICE_OFFSET))
    else:
        sl_limit = round_price(pos.symbol, sl_price * (1 + SL_PRICE_OFFSET))
        tp_limit = round_price(pos.symbol, tp_price * (1 + TP_PRICE_OFFSET))

    validate_order(pos.symbol, qty, sl_price)

    # Validate price relationship: for BUY OCO tp_limit < currentPrice < sl_price
    # for SELL OCO sl_price < currentPrice < tp_limit
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
    return result


async def close_position(position_id: int) -> dict:
    pos = _get_position(position_id)
    side = _close_side(pos)
    qty = _close_qty(pos)

    kwargs = dict(
        symbol=pos.symbol, side=side, type="MARKET",
        quantity=str(qty), newClientOrderId=_client_order_id("close", pos.id),
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
        order_type="MARKET", status="NEW", quantity=qty,
        market_type=pos.market_type, purpose="CLOSE", position_id=pos.id,
    )

    log.info("close_placed", symbol=pos.symbol, side=side, qty=str(qty), order_id=order_id)
    return result


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

    # Clean position refs
    if order.position_id:
        pos = _get_position(order.position_id) if order.position_id else None
        if pos:
            updates = {}
            if pos.sl_order_id == order.binance_order_id:
                updates["sl_order_id"] = None
            if pos.tp_order_id == order.binance_order_id:
                updates["tp_order_id"] = None
            if updates:
                await _update_position_order_ref(pos, **updates)

    log.info("order_cancelled", order_id=order.binance_order_id, symbol=order.symbol)
    return result


async def cancel_position_orders(position_id: int) -> dict:
    pos = _get_position(position_id)
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

    log.info("position_orders_cancelled", symbol=pos.symbol, position_id=position_id, cancelled=cancelled)
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
