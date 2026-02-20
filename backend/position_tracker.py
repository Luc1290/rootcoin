import time as _time
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select

from backend import binance_client, ws_manager
from backend.config import settings
from backend.database import async_session
from backend.models import Position, Trade
from backend.ws_manager import EVENT_EXECUTION_REPORT, EVENT_PRICE_UPDATE

log = structlog.get_logger()

DUST_THRESHOLD_USD = Decimal("5")
MIN_MARGIN_LONG_USD = Decimal("50")
SHORT_CLOSE_GRACE = 300

_positions: dict[int, Position] = {}
_recent_short_closes: dict[str, float] = {}


# --- Public API ---


async def start():
    await _scan_existing_positions()
    ws_manager.on(EVENT_EXECUTION_REPORT, _handle_execution_report)
    ws_manager.on(EVENT_PRICE_UPDATE, _handle_price_update)
    log.info("position_tracker_started")


async def stop():
    _positions.clear()
    log.info("position_tracker_stopped")


def get_positions() -> list[Position]:
    return list(_positions.values())


# --- Helpers ---


def _extract_base_asset(symbol: str) -> str:
    for quote in ("USDC", "USDT", "BUSD", "FDUSD", "TUSD", "DAI"):
        if symbol.endswith(quote):
            return symbol[: -len(quote)]
    return symbol


def _asset_to_symbol(asset: str) -> str:
    return f"{asset}USDC"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _save_position(position: Position):
    async with async_session() as session:
        await session.merge(position)
        await session.commit()


def _compute_pnl(pos: Position, price: Decimal) -> tuple[Decimal, Decimal]:
    if pos.entry_price <= 0:
        return Decimal("0"), Decimal("0")
    if pos.side == "LONG":
        pnl_usd = (price - pos.entry_price) * pos.quantity
        pnl_pct = ((price / pos.entry_price) - 1) * 100
    else:
        pnl_usd = (pos.entry_price - price) * pos.quantity
        pnl_pct = ((pos.entry_price / price) - 1) * 100 if price > 0 else Decimal("0")
    return pnl_usd, pnl_pct


def _is_dust(quantity: Decimal, price: Decimal) -> bool:
    if quantity <= 0:
        return True
    return quantity * price < DUST_THRESHOLD_USD


def _find_position(symbol: str, market_type: str) -> Position | None:
    for pos in _positions.values():
        if pos.symbol == symbol and pos.market_type == market_type and pos.is_active:
            return pos
    return None


# --- Scan at startup ---


async def _scan_existing_positions():
    async with async_session() as session:
        result = await session.execute(select(Position).where(Position.is_active == True))
        db_positions = {p.id: p for p in result.scalars().all()}

    found_keys: set[tuple[str, str]] = set()
    stables = settings.stablecoins_set

    # Spot
    try:
        for bal in await binance_client.get_spot_balances():
            asset = bal["asset"]
            if asset in stables or asset == "BNB":
                continue
            total = Decimal(bal["free"]) + Decimal(bal["locked"])
            if total <= 0:
                continue
            symbol = _asset_to_symbol(asset)
            if _is_dust(total, Decimal("0")):
                continue
            found_keys.add((symbol, "SPOT"))
            await _upsert_scanned(symbol, "LONG", total, "SPOT", db_positions)
    except Exception:
        log.error("scan_spot_failed", exc_info=True)

    # Cross margin
    try:
        for bal in await binance_client.get_cross_margin_balances():
            asset = bal["asset"]
            if asset in stables:
                continue
            free = Decimal(bal.get("free", "0"))
            locked = Decimal(bal.get("locked", "0"))
            borrowed = Decimal(bal.get("borrowed", "0"))
            symbol = _asset_to_symbol(asset)
            if borrowed > 0:
                found_keys.add((symbol, "CROSS_MARGIN"))
                await _upsert_scanned(symbol, "SHORT", borrowed, "CROSS_MARGIN", db_positions)
            elif free + locked > 0:
                total = free + locked
                if _is_dust(total, Decimal("0")):
                    continue
                found_keys.add((symbol, "CROSS_MARGIN"))
                await _upsert_scanned(symbol, "LONG", total, "CROSS_MARGIN", db_positions)
    except Exception:
        log.error("scan_cross_margin_failed", exc_info=True)

    # Isolated margin
    try:
        for pair in await binance_client.get_isolated_margin_balances():
            symbol = pair.get("symbol", "")
            base = pair.get("baseAsset", {})
            base_borrowed = Decimal(base.get("borrowed", "0"))
            base_free = Decimal(base.get("free", "0"))
            base_locked = Decimal(base.get("locked", "0"))
            if base_borrowed > 0:
                found_keys.add((symbol, "ISOLATED_MARGIN"))
                await _upsert_scanned(symbol, "SHORT", base_borrowed, "ISOLATED_MARGIN", db_positions)
            elif base_free + base_locked > 0:
                total = base_free + base_locked
                if _is_dust(total, Decimal("0")):
                    continue
                found_keys.add((symbol, "ISOLATED_MARGIN"))
                await _upsert_scanned(symbol, "LONG", total, "ISOLATED_MARGIN", db_positions)
    except Exception:
        log.error("scan_isolated_margin_failed", exc_info=True)

    # Close stale DB positions not found on Binance
    async with async_session() as session:
        for pos in db_positions.values():
            if pos.is_active and (pos.symbol, pos.market_type) not in found_keys:
                pos.is_active = False
                pos.updated_at = _now()
                await session.merge(pos)
                log.info("position_closed_stale", symbol=pos.symbol, market_type=pos.market_type)
        await session.commit()

    for symbol, _ in found_keys:
        await ws_manager.subscribe_symbol(symbol)

    log.info("position_scan_complete", count=len(_positions))


async def _upsert_scanned(
    symbol: str, side: str, quantity: Decimal, market_type: str, db_positions: dict
):
    existing = None
    for pos in db_positions.values():
        if pos.symbol == symbol and pos.market_type == market_type and pos.is_active:
            existing = pos
            break

    if existing:
        existing.quantity = quantity
        existing.side = side
        existing.updated_at = _now()
        await _save_position(existing)
        _positions[existing.id] = existing
        log.info("position_synced", symbol=symbol, side=side, qty=str(quantity))
    else:
        entry_price = await _calculate_entry_price(symbol, side, quantity, market_type)
        pos = Position(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            market_type=market_type,
            opened_at=_now(),
            is_active=True,
        )
        async with async_session() as session:
            session.add(pos)
            await session.commit()
            await session.refresh(pos)
        _positions[pos.id] = pos
        log.info("position_detected", symbol=symbol, side=side, entry=str(entry_price), qty=str(quantity))


# --- Entry price calculation ---


async def _calculate_entry_price(
    symbol: str, side: str, quantity: Decimal, market_type: str
) -> Decimal:
    try:
        if market_type == "SPOT":
            trades = await binance_client.get_my_trades(symbol)
        else:
            trades = await binance_client.get_margin_trades(
                symbol, is_isolated=(market_type == "ISOLATED_MARGIN")
            )
    except Exception:
        log.warning("entry_price_fetch_failed", symbol=symbol)
        return Decimal("0")

    if not trades:
        return Decimal("0")

    trades.sort(key=lambda t: t["time"], reverse=True)
    target_buyer = side == "LONG"
    base_asset = _extract_base_asset(symbol)

    accumulated = Decimal("0")
    weighted = Decimal("0")

    for t in trades:
        if t["isBuyer"] != target_buyer:
            continue
        qty = Decimal(t["qty"])
        price = Decimal(t["price"])
        # Adjust for fee in base asset (reduces actual received qty)
        comm = Decimal(t.get("commission", "0"))
        comm_asset = t.get("commissionAsset", "")
        if comm_asset == base_asset and target_buyer:
            qty -= comm

        remaining = quantity - accumulated
        used = min(qty, remaining)
        weighted += used * price
        accumulated += used
        if accumulated >= quantity:
            break

    if accumulated <= 0:
        return Decimal("0")
    return weighted / accumulated


# --- Execution report handler ---


async def _determine_market_type(symbol: str) -> str:
    for pos in _positions.values():
        if pos.symbol == symbol and pos.is_active:
            return pos.market_type

    base_asset = _extract_base_asset(symbol)
    try:
        for bal in await binance_client.get_cross_margin_balances():
            if bal["asset"] == base_asset:
                if Decimal(bal.get("borrowed", "0")) > 0 or Decimal(bal.get("free", "0")) > 0:
                    return "CROSS_MARGIN"
    except Exception:
        pass
    try:
        for pair in await binance_client.get_isolated_margin_balances():
            if pair.get("symbol") == symbol:
                base = pair.get("baseAsset", {})
                if Decimal(base.get("borrowed", "0")) > 0 or Decimal(base.get("free", "0")) > 0:
                    return "ISOLATED_MARGIN"
    except Exception:
        pass
    return "SPOT"


async def _handle_execution_report(msg: dict):
    status = msg.get("X", "")
    if status not in ("PARTIALLY_FILLED", "FILLED"):
        return

    symbol = msg.get("s", "")
    side = msg.get("S", "")
    fill_qty = Decimal(msg.get("l", "0"))
    fill_price = Decimal(msg.get("L", "0"))
    commission = Decimal(msg.get("n", "0"))
    commission_asset = msg.get("N", "")
    trade_id = str(msg.get("t", ""))
    order_id = str(msg.get("i", ""))

    if fill_qty <= 0:
        return

    log.info("execution_report", symbol=symbol, side=side, qty=str(fill_qty),
             price=str(fill_price), status=status)

    market_type = await _determine_market_type(symbol)

    await _record_trade(
        trade_id=trade_id, order_id=order_id, symbol=symbol, side=side,
        price=fill_price, quantity=fill_qty, commission=commission,
        commission_asset=commission_asset, market_type=market_type,
        is_maker=msg.get("m", False),
    )

    # Adjust qty for fee in base asset
    base_asset = _extract_base_asset(symbol)
    effective_qty = fill_qty
    if commission_asset == base_asset and side == "BUY":
        effective_qty = fill_qty - commission

    position = _find_position(symbol, market_type)

    if side == "BUY":
        await _handle_buy(symbol, effective_qty, fill_price, market_type, position)
    else:
        await _handle_sell(symbol, effective_qty, fill_price, market_type, position)


async def _handle_buy(
    symbol: str, qty: Decimal, price: Decimal, market_type: str, position: Position | None
):
    if market_type == "SPOT":
        if position and position.side == "LONG":
            await _dca(position, qty, price)
        else:
            await _open_position(symbol, "LONG", qty, price, market_type)
        return

    # Margin BUY: close SHORT or open LONG?
    if position and position.side == "SHORT":
        await _reduce_or_close(position, qty, price)
        return

    # Check anti-false-LONG after SHORT close
    last_close = _recent_short_closes.get(symbol, 0)
    if _time.monotonic() - last_close < SHORT_CLOSE_GRACE:
        if qty * price < MIN_MARGIN_LONG_USD:
            log.info("ignoring_post_short_residual", symbol=symbol)
            return

    if position and position.side == "LONG":
        await _dca(position, qty, price)
    else:
        if qty * price < MIN_MARGIN_LONG_USD:
            log.info("ignoring_small_margin_long", symbol=symbol, notional=str(qty * price))
            return
        await _open_position(symbol, "LONG", qty, price, market_type)


async def _handle_sell(
    symbol: str, qty: Decimal, price: Decimal, market_type: str, position: Position | None
):
    if market_type == "SPOT":
        if position and position.side == "LONG":
            await _reduce_or_close(position, qty, price)
        else:
            log.warning("spot_sell_no_position", symbol=symbol)
        return

    # Margin SELL: close LONG or open SHORT?
    if position and position.side == "LONG":
        await _reduce_or_close(position, qty, price)
    elif position and position.side == "SHORT":
        await _dca(position, qty, price)
    else:
        await _open_position(symbol, "SHORT", qty, price, market_type)


# --- Position operations ---


async def _open_position(
    symbol: str, side: str, qty: Decimal, price: Decimal, market_type: str
):
    pos = Position(
        symbol=symbol,
        side=side,
        entry_price=price,
        quantity=qty,
        market_type=market_type,
        current_price=price,
        pnl_usd=Decimal("0"),
        pnl_pct=Decimal("0"),
        opened_at=_now(),
        is_active=True,
    )
    async with async_session() as session:
        session.add(pos)
        await session.commit()
        await session.refresh(pos)
    _positions[pos.id] = pos
    await ws_manager.subscribe_symbol(symbol)
    log.info("position_opened", symbol=symbol, side=side, price=str(price), qty=str(qty),
             market_type=market_type)


async def _dca(position: Position, qty: Decimal, price: Decimal):
    old_value = position.quantity * position.entry_price
    new_value = qty * price
    position.quantity = position.quantity + qty
    position.entry_price = (old_value + new_value) / position.quantity
    position.updated_at = _now()
    await _save_position(position)
    log.info("position_dca", symbol=position.symbol, avg_price=str(position.entry_price),
             qty=str(position.quantity))


async def _reduce_or_close(position: Position, qty: Decimal, price: Decimal):
    if position.side == "LONG":
        realized = (price - position.entry_price) * qty
    else:
        realized = (position.entry_price - price) * qty

    new_qty = position.quantity - qty

    if _is_dust(new_qty, price):
        position.is_active = False
        position.quantity = Decimal("0")
        position.updated_at = _now()
        await _save_position(position)
        del _positions[position.id]

        if position.side == "SHORT" and position.market_type != "SPOT":
            _recent_short_closes[position.symbol] = _time.monotonic()

        log.info("position_closed", symbol=position.symbol, side=position.side,
                 pnl=str(realized))
    else:
        position.quantity = new_qty
        position.updated_at = _now()
        await _save_position(position)
        log.info("position_reduced", symbol=position.symbol, remaining=str(new_qty),
                 realized_pnl=str(realized))


# --- Price updates ---


async def _handle_price_update(msg: dict):
    symbol = msg.get("s", "")
    price_str = msg.get("c", "0")
    price = Decimal(price_str)
    if price <= 0:
        return

    for pos in _positions.values():
        if pos.symbol == symbol and pos.is_active:
            pos.current_price = price
            pos.pnl_usd, pos.pnl_pct = _compute_pnl(pos, price)


# --- Record trade to DB ---


async def _record_trade(
    trade_id: str, order_id: str, symbol: str, side: str, price: Decimal,
    quantity: Decimal, commission: Decimal, commission_asset: str,
    market_type: str, is_maker: bool,
):
    async with async_session() as session:
        existing = await session.execute(
            select(Trade).where(Trade.binance_trade_id == trade_id)
        )
        if existing.scalar_one_or_none():
            return

        trade = Trade(
            binance_trade_id=trade_id,
            binance_order_id=order_id,
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            quote_qty=price * quantity,
            commission=commission,
            commission_asset=commission_asset,
            market_type=market_type,
            is_maker=is_maker,
            executed_at=_now(),
        )
        session.add(trade)
        await session.commit()
