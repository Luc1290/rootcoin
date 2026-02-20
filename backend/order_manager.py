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
    qty = round_quantity(pos.symbol, pos.quantity)

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
    qty = round_quantity(pos.symbol, pos.quantity)

    if pos.side == "LONG":
        limit_price = round_price(pos.symbol, tp_price * (1 + TP_PRICE_OFFSET))
    else:
        limit_price = round_price(pos.symbol, tp_price * (1 - TP_PRICE_OFFSET))
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
    qty = round_quantity(pos.symbol, pos.quantity)
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
        tp_limit = round_price(pos.symbol, tp_price * (1 + TP_PRICE_OFFSET))
    else:
        sl_limit = round_price(pos.symbol, sl_price * (1 + SL_PRICE_OFFSET))
        tp_limit = round_price(pos.symbol, tp_price * (1 - TP_PRICE_OFFSET))

    validate_order(pos.symbol, qty, sl_price)

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
        binance_order_id="", symbol=pos.symbol, side=side,
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
    qty = round_quantity(pos.symbol, pos.quantity)

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


async def cancel_order_by_binance_id(symbol: str, order_id: str, market_type: str) -> dict:
    if market_type == "SPOT":
        return await binance_client.cancel_order(symbol, order_id)
    else:
        return await binance_client.cancel_margin_order(
            symbol, order_id, is_isolated=(market_type == "ISOLATED_MARGIN"),
        )


async def _cancel_oco(symbol: str, order_list_id: str, market_type: str):
    client = await binance_client.get_client()
    if market_type == "SPOT":
        await client.cancel_order_list(symbol=symbol, orderListId=order_list_id)
    else:
        kwargs = {"symbol": symbol, "orderListId": order_list_id}
        if market_type == "ISOLATED_MARGIN":
            kwargs["isIsolated"] = "TRUE"
        await client.cancel_margin_order_list(**kwargs)
