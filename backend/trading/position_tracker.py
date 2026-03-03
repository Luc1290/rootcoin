import asyncio
import time as _time
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select

from backend.exchange import binance_client, ws_manager
from backend.trading import order_manager, pnl
from backend.trading import position_reconciler
from backend.services import journal_snapshotter, telegram_notifier
from backend.core.config import settings
from backend.core.database import async_session
from backend.core.models import Position, Trade
from backend.exchange.ws_manager import EVENT_EXECUTION_REPORT, EVENT_LIST_STATUS, EVENT_PRICE_UPDATE

log = structlog.get_logger()

DUST_THRESHOLD_USD = Decimal("5")
MIN_MARGIN_LONG_USD = Decimal("50")
SHORT_CLOSE_GRACE = 300
RESIDUAL_SELL_THRESHOLD_USD = Decimal("15")
RESIDUAL_SELL_RETRIES = 3
RESIDUAL_SELL_DELAY = 2

_positions: dict[int, Position] = {}
_recent_short_closes: dict[str, float] = {}
_reconciled: bool = False
_reconcile_task: asyncio.Task | None = None
_periodic_reconcile_task: asyncio.Task | None = None
_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# --- Public API ---


async def start():
    await position_reconciler.fast_load_from_db()
    ws_manager.on(EVENT_EXECUTION_REPORT, _handle_execution_report)
    ws_manager.on(EVENT_LIST_STATUS, _handle_list_status)
    ws_manager.on(EVENT_PRICE_UPDATE, _handle_price_update)
    log.info("position_tracker_started", positions=len(_positions))
    global _reconcile_task, _periodic_reconcile_task
    _reconcile_task = asyncio.create_task(position_reconciler.background_reconcile())
    _periodic_reconcile_task = asyncio.create_task(position_reconciler.periodic_reconcile_loop())


async def stop():
    global _reconcile_task, _periodic_reconcile_task, _reconciled
    for task in (_reconcile_task, _periodic_reconcile_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    _reconcile_task = None
    _periodic_reconcile_task = None
    _reconciled = False
    _positions.clear()
    log.info("position_tracker_stopped")


def get_positions() -> list[Position]:
    return list(_positions.values())


def is_reconciled() -> bool:
    return _reconciled


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


async def _commission_to_usd(commission: Decimal, commission_asset: str, fill_price: Decimal, symbol: str) -> Decimal:
    if commission <= 0:
        return Decimal("0")
    stables = settings.stablecoins_set
    if commission_asset in stables:
        return commission
    base = _extract_base_asset(symbol)
    if commission_asset == base:
        return commission * fill_price
    # Other asset (BNB, etc.) — get real price from in-memory or API
    asset_symbol = f"{commission_asset}USDC"
    for pos in _positions.values():
        if pos.symbol == asset_symbol and pos.current_price and pos.current_price > 0:
            return commission * pos.current_price
    # Fallback: fetch price from Binance REST API
    try:
        client = await binance_client.get_client()
        ticker = await client.get_symbol_ticker(symbol=asset_symbol)
        price = Decimal(ticker["price"])
        if price > 0:
            return commission * price
    except Exception:
        pass
    # Last resort: try USDT pair
    try:
        asset_usdt = f"{commission_asset}USDT"
        client = await binance_client.get_client()
        ticker = await client.get_symbol_ticker(symbol=asset_usdt)
        price = Decimal(ticker["price"])
        if price > 0:
            return commission * price
    except Exception:
        pass
    log.warning("commission_conversion_failed", asset=commission_asset, amount=str(commission))
    return Decimal("0")


def _is_dust(quantity: Decimal, price: Decimal) -> bool:
    if quantity <= 0:
        return True
    return quantity * price < DUST_THRESHOLD_USD


def _find_position(symbol: str, market_type: str) -> Position | None:
    for pos in _positions.values():
        if pos.symbol == symbol and pos.market_type == market_type and pos.is_active:
            return pos
    return None




async def _clean_order_ref(order_id: str):
    for pos in _positions.values():
        updates = {}
        if pos.sl_order_id == order_id:
            updates["sl_order_id"] = None
        if pos.tp_order_id == order_id:
            updates["tp_order_id"] = None
        if updates:
            for k, v in updates.items():
                setattr(pos, k, v)
            await _save_position(pos)
            log.info("order_ref_cleaned", symbol=pos.symbol, order_id=order_id, cleared=list(updates.keys()))


# --- Execution report handler ---


async def _determine_market_type(symbol: str, order_id: str = "") -> str:
    for pos in _positions.values():
        if pos.symbol == symbol and pos.is_active:
            return pos.market_type

    base_asset = _extract_base_asset(symbol)
    try:
        for bal in await binance_client.get_cross_margin_balances():
            if bal["asset"] == base_asset:
                borrowed = Decimal(bal.get("borrowed", "0"))
                free = Decimal(bal.get("free", "0"))
                locked = Decimal(bal.get("locked", "0"))
                if borrowed > 0 or free > 0 or locked > 0:
                    return "CROSS_MARGIN"
    except Exception:
        pass
    try:
        for pair in await binance_client.get_isolated_margin_balances():
            if pair.get("symbol") == symbol:
                base = pair.get("baseAsset", {})
                borrowed = Decimal(base.get("borrowed", "0"))
                free = Decimal(base.get("free", "0"))
                locked = Decimal(base.get("locked", "0"))
                if borrowed > 0 or free > 0 or locked > 0:
                    return "ISOLATED_MARGIN"
    except Exception:
        pass

    if order_id:
        client = await binance_client.get_client()
        try:
            await client.get_margin_order(symbol=symbol, orderId=order_id)
            return "CROSS_MARGIN"
        except Exception:
            pass
        try:
            await client.get_margin_order(
                symbol=symbol, orderId=order_id, isIsolated="TRUE",
            )
            return "ISOLATED_MARGIN"
        except Exception:
            pass

    return "SPOT"


async def _handle_execution_report(msg: dict):
    status = msg.get("X", "")

    # Handle order cancellation/expiration — clean position refs + update DB
    if status in ("CANCELED", "EXPIRED", "REJECTED"):
        order_id = str(msg.get("i", ""))
        if order_id:
            await _clean_order_ref(order_id)
            await order_manager.mark_order_status(order_id, status)
        return

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

    market_type = await _determine_market_type(symbol, order_id)

    await _record_trade(
        trade_id=trade_id, order_id=order_id, symbol=symbol, side=side,
        price=fill_price, quantity=fill_qty, commission=commission,
        commission_asset=commission_asset, market_type=market_type,
        is_maker=msg.get("m", False),
    )

    position = _find_position(symbol, market_type)

    # Adjust qty for fee in base asset — only for LONG buys (commission
    # reduces received qty).  For SHORT closes the full fill_qty covers the
    # debt; commission is a separate cost tracked in fee_usd.
    base_asset = _extract_base_asset(symbol)
    effective_qty = fill_qty
    if commission_asset == base_asset and side == "BUY":
        closing_short = position and position.side == "SHORT"
        if not closing_short:
            effective_qty = fill_qty - commission

    fee_usd = await _commission_to_usd(commission, commission_asset, fill_price, symbol)

    if side == "BUY":
        await _handle_buy(symbol, effective_qty, fill_price, market_type, position, fee_usd, order_id)
    else:
        await _handle_sell(symbol, effective_qty, fill_price, market_type, position, fee_usd, order_id)


async def _handle_buy(
    symbol: str, qty: Decimal, price: Decimal, market_type: str,
    position: Position | None, fee_usd: Decimal = Decimal("0"),
    order_id: str = "",
):
    if market_type == "SPOT":
        if position and position.side == "LONG":
            await _dca(position, qty, price, fee_usd)
        else:
            await _open_position(symbol, "LONG", qty, price, market_type, fee_usd)
        return

    # Margin BUY: close SHORT or open LONG?
    if position and position.side == "SHORT":
        await _reduce_or_close(position, qty, price, fee_usd, order_id)
        return

    # Check anti-false-LONG after SHORT close (+ cleanup expired entries)
    now = _time.monotonic()
    expired = [s for s, t in _recent_short_closes.items() if now - t >= SHORT_CLOSE_GRACE]
    for s in expired:
        del _recent_short_closes[s]
    last_close = _recent_short_closes.get(symbol, 0)
    if now - last_close < SHORT_CLOSE_GRACE:
        if qty * price < MIN_MARGIN_LONG_USD:
            log.info("ignoring_post_short_residual", symbol=symbol)
            return

    if position and position.side == "LONG":
        await _dca(position, qty, price, fee_usd)
    else:
        if qty * price < MIN_MARGIN_LONG_USD:
            log.info("ignoring_small_margin_long", symbol=symbol, notional=str(qty * price))
            return
        await _open_position(symbol, "LONG", qty, price, market_type, fee_usd)


async def _handle_sell(
    symbol: str, qty: Decimal, price: Decimal, market_type: str,
    position: Position | None, fee_usd: Decimal = Decimal("0"),
    order_id: str = "",
):
    if market_type == "SPOT":
        if position and position.side == "LONG":
            await _reduce_or_close(position, qty, price, fee_usd, order_id)
        else:
            log.warning("spot_sell_no_position", symbol=symbol)
        return

    # Margin SELL: close LONG or open SHORT?
    if position and position.side == "LONG":
        await _reduce_or_close(position, qty, price, fee_usd, order_id)
    elif position and position.side == "SHORT":
        await _dca(position, qty, price, fee_usd)
    else:
        if qty * price < MIN_MARGIN_LONG_USD:
            log.info("ignoring_small_margin_short", symbol=symbol, notional=str(qty * price))
            return
        await _open_position(symbol, "SHORT", qty, price, market_type, fee_usd)


# --- Position operations ---


async def _open_position(
    symbol: str, side: str, qty: Decimal, price: Decimal, market_type: str,
    fee_usd: Decimal = Decimal("0"),
):
    pos = Position(
        symbol=symbol,
        side=side,
        entry_price=price,
        quantity=qty,
        entry_quantity=qty,
        market_type=market_type,
        current_price=price,
        pnl_usd=Decimal("0"),
        pnl_pct=Decimal("0"),
        entry_fees_usd=fee_usd,
        opened_at=_now(),
        is_active=True,
    )
    async with async_session() as session:
        session.add(pos)
        await session.commit()
        await session.refresh(pos)
    _positions[pos.id] = pos
    await ws_manager.subscribe_symbol(symbol)
    try:
        await journal_snapshotter.capture_snapshot(
            pos.id, "OPEN", symbol, side, price, qty,
        )
    except Exception:
        log.warning("open_snapshot_failed", position_id=pos.id, exc_info=True)
    log.info("position_opened", symbol=symbol, side=side, price=str(price), qty=str(qty),
             market_type=market_type)
    asyncio.create_task(telegram_notifier.notify_position_opened(symbol, side, price, qty, market_type))


async def _dca(position: Position, qty: Decimal, price: Decimal, fee_usd: Decimal = Decimal("0")):
    old_value = position.quantity * position.entry_price
    new_value = qty * price
    position.quantity = position.quantity + qty
    position.entry_price = (old_value + new_value) / position.quantity
    position.entry_fees_usd = (position.entry_fees_usd or Decimal("0")) + fee_usd
    position.entry_quantity = (position.entry_quantity or Decimal("0")) + qty
    position.updated_at = _now()
    await _save_position(position)
    try:
        await journal_snapshotter.capture_snapshot(
            position.id, "DCA", position.symbol, position.side, price, qty,
        )
    except Exception:
        log.warning("dca_snapshot_failed", position_id=position.id, exc_info=True)
    log.info("position_dca", symbol=position.symbol, avg_price=str(position.entry_price),
             qty=str(position.quantity))
    asyncio.create_task(telegram_notifier.notify_position_dca(
        position.symbol, position.side, price, qty, position.entry_price, position.quantity,
    ))


def _exit_reason(position: Position, order_id: str) -> str:
    if not order_id:
        return ""
    if position.sl_order_id and order_id == position.sl_order_id:
        return "SL"
    if position.tp_order_id and order_id == position.tp_order_id:
        return "TP"
    if position.oco_order_list_id:
        return "OCO"
    return ""


async def _reduce_or_close(
    position: Position, qty: Decimal, price: Decimal,
    fee_usd: Decimal = Decimal("0"), order_id: str = "",
):
    realized = pnl.gross_pnl(position.side, position.entry_price, price, qty)

    position.realized_pnl = (position.realized_pnl or Decimal("0")) + realized
    position.exit_fees_usd = (position.exit_fees_usd or Decimal("0")) + fee_usd
    position.exit_price = price

    new_qty = position.quantity - qty

    if _is_dust(new_qty, price):
        position.realized_pnl_pct = pnl.realized_pnl_pct(
            position.realized_pnl, position.entry_fees_usd,
            position.exit_fees_usd, position.entry_quantity,
            position.quantity, position.entry_price,
        )
        net_pnl = pnl.net_realized_pnl(
            position.realized_pnl, position.entry_fees_usd, position.exit_fees_usd,
        )
        position.closed_at = _now()
        position.is_active = False
        position.quantity = Decimal("0")
        position.updated_at = _now()
        await _save_position(position)
        try:
            await journal_snapshotter.capture_snapshot(
                position.id, "CLOSE", position.symbol, position.side, price, qty,
            )
        except Exception:
            log.warning("close_snapshot_failed", position_id=position.id, exc_info=True)
        del _positions[position.id]
        clear_pnl_alerts(position.id)

        if position.side == "SHORT" and position.market_type != "SPOT":
            _recent_short_closes[position.symbol] = _time.monotonic()
        if position.market_type != "SPOT":
            _fire_and_forget(
                _sell_margin_residual(position.symbol, position.market_type, position.side)
            )

        reason = _exit_reason(position, order_id)
        log.info("position_closed", symbol=position.symbol, side=position.side,
                 pnl=str(realized), net_pnl=str(net_pnl), exit_reason=reason)
        asyncio.create_task(telegram_notifier.notify_position_closed(
            position.symbol, position.side, position.entry_price, price,
            position.realized_pnl, net_pnl, position.realized_pnl_pct,
            position.opened_at, position.closed_at, reason,
        ))
    else:
        position.quantity = new_qty
        position.updated_at = _now()
        await _save_position(position)
        log.info("position_reduced", symbol=position.symbol, remaining=str(new_qty),
                 realized_pnl=str(realized))


async def _sell_margin_residual(symbol: str, market_type: str, side: str = "LONG"):
    from backend.exchange.symbol_filters import round_quantity

    base_asset = _extract_base_asset(symbol)

    for attempt in range(1, RESIDUAL_SELL_RETRIES + 1):
        await asyncio.sleep(RESIDUAL_SELL_DELAY)
        try:
            if market_type == "CROSS_MARGIN":
                balances = await binance_client.get_cross_margin_balances()
                bal = next((b for b in balances if b["asset"] == base_asset), None)
                if not bal:
                    return
                free = Decimal(bal.get("free", "0"))
                borrowed = Decimal(bal.get("borrowed", "0"))
            else:
                pairs = await binance_client.get_isolated_margin_balances()
                pair = next((p for p in pairs if p.get("symbol") == symbol), None)
                if not pair:
                    return
                base_info = pair.get("baseAsset", {})
                free = Decimal(base_info.get("free", "0"))
                borrowed = Decimal(base_info.get("borrowed", "0"))

            # SHORT residual: remaining borrowed amount (from commission on close BUY)
            if side == "SHORT" and borrowed > 0:
                await binance_client.repay_margin_loan(
                    asset=base_asset, amount=borrowed,
                    is_isolated=(market_type == "ISOLATED_MARGIN"),
                    symbol=symbol if market_type == "ISOLATED_MARGIN" else None,
                )
                log.info("margin_short_residual_repaid", symbol=symbol,
                         asset=base_asset, amount=str(borrowed))
                return

            # LONG residual: remaining free base asset
            if free <= 0:
                return

            client = await binance_client.get_client()
            ticker = await client.get_symbol_ticker(symbol=symbol)
            price = Decimal(ticker["price"])
            value = free * price

            if value < RESIDUAL_SELL_THRESHOLD_USD:
                log.info("margin_residual_below_threshold", symbol=symbol,
                         qty=str(free), value_usd=str(value))
                return

            qty = round_quantity(symbol, free)
            if qty <= 0:
                return

            kwargs = dict(
                symbol=symbol, side="SELL", type="MARKET", quantity=str(qty),
                sideEffectType="AUTO_REPAY",
            )
            if market_type == "ISOLATED_MARGIN":
                kwargs["isIsolated"] = "TRUE"

            await binance_client.place_margin_order(**kwargs)
            log.info("margin_residual_sold", symbol=symbol, qty=str(qty),
                     value_usd=str(value))
            return

        except Exception:
            log.warning("margin_residual_cleanup_failed", symbol=symbol,
                        side=side, attempt=attempt, exc_info=True)

    log.error("margin_residual_cleanup_exhausted", symbol=symbol, side=side)


async def _handle_list_status(msg: dict):
    list_status = msg.get("l", "")
    order_list_id = str(msg.get("g", ""))
    if list_status == "ALL_DONE" and order_list_id:
        for pos in _positions.values():
            if pos.oco_order_list_id == order_list_id:
                pos.oco_order_list_id = None
                await _save_position(pos)
                log.info("oco_ref_cleaned", symbol=pos.symbol, order_list_id=order_list_id)
                break
        await order_manager.mark_oco_done(order_list_id)


# --- Price updates & PnL threshold alerts ---

_PNL_THRESHOLDS = [-2.0, -1.7, -1.3, -0.8, -0.5, 0.5, 0.8, 1.3, 1.7, 2.0]
_PNL_COOLDOWN = 600  # 10 min cooldown per (position, threshold)
_pnl_cooldowns: dict[tuple[int, float], float] = {}  # (pos_id, threshold) -> monotonic ts


async def _handle_price_update(msg: dict):
    symbol = msg.get("s", "")
    price_str = msg.get("c", "0")
    price = Decimal(price_str)
    if price <= 0:
        return

    for pos in _positions.values():
        if pos.symbol == symbol and pos.is_active:
            prev_pct = pos.pnl_pct
            pos.current_price = price
            pos.pnl_usd, pos.pnl_pct = pnl.unrealized_pnl(
                pos.side, pos.entry_price, price, pos.quantity, pos.entry_fees_usd,
            )
            if prev_pct is not None:
                _check_pnl_thresholds(pos, float(prev_pct), float(pos.pnl_pct))


def _check_pnl_thresholds(pos: Position, prev_pct: float, cur_pct: float):
    now = _time.monotonic()
    for t in _PNL_THRESHOLDS:
        crossed = (prev_pct < t <= cur_pct) or (prev_pct > t >= cur_pct)
        if not crossed:
            continue
        key = (pos.id, t)
        if now - _pnl_cooldowns.get(key, 0) < _PNL_COOLDOWN:
            continue
        _pnl_cooldowns[key] = now
        asyncio.create_task(telegram_notifier.notify_pnl_threshold(
            pos.symbol, pos.side, pos.pnl_pct, pos.pnl_usd,
            pos.entry_price, pos.current_price, t,
        ))


def clear_pnl_alerts(position_id: int):
    dead = [k for k in _pnl_cooldowns if k[0] == position_id]
    for k in dead:
        del _pnl_cooldowns[k]


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
