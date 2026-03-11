import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select

from backend.exchange import binance_client, ws_manager
from backend.trading import order_manager, pnl
from backend.services import telegram_notifier
from backend.core.config import settings
from backend.core.database import async_session
from backend.core.models import Position, Trade

log = structlog.get_logger()

PERIODIC_RECONCILE_INTERVAL = 1800  # 30 min
RESIDUAL_THRESHOLD_USD = Decimal("30")


def _tracker():
    from backend.trading import position_tracker
    return position_tracker


# --- Fast load + background reconciliation ---


async def fast_load_from_db():
    tracker = _tracker()
    async with async_session() as session:
        result = await session.execute(select(Position).where(Position.is_active == True))
        for pos in result.scalars().all():
            tracker._positions[pos.id] = pos
    symbols = {pos.symbol for pos in tracker._positions.values()}
    for symbol in symbols:
        await ws_manager.subscribe_symbol(symbol)
    log.info("fast_load_complete", positions=len(tracker._positions))


async def background_reconcile():
    tracker = _tracker()
    try:
        log.info("reconciliation_starting")
        await _fix_closed_positions()
        await _reconcile_positions()
        await _verify_order_refs()
        await _backfill_all_trades()
        tracker._reconciled = True
        log.info("reconciliation_complete")
    except asyncio.CancelledError:
        raise
    except Exception:
        log.error("reconciliation_failed", exc_info=True)
        await asyncio.sleep(30)
        if not tracker._reconciled:
            asyncio.create_task(background_reconcile())


async def periodic_reconcile_loop():
    tracker = _tracker()
    while not tracker._reconciled:
        await asyncio.sleep(5)
    while True:
        await asyncio.sleep(PERIODIC_RECONCILE_INTERVAL)
        if not tracker._positions:
            continue
        try:
            log.info("periodic_reconcile_starting")
            await _reconcile_positions()
            log.info("periodic_reconcile_done", positions=len(tracker._positions))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("periodic_reconcile_failed", exc_info=True)


async def _fix_closed_positions():
    tracker = _tracker()
    from sqlalchemy import or_
    async with async_session() as session:
        result = await session.execute(
            select(Position).where(
                Position.is_active == False,
                Position.realized_pnl.isnot(None),
                or_(Position.closed_at.is_(None), Position.realized_pnl_pct.is_(None)),
            )
        )
        for pos in result.scalars().all():
            if not pos.closed_at:
                pos.closed_at = pos.updated_at or tracker._now()
            if pos.realized_pnl_pct is None:
                pos.realized_pnl_pct = pnl.realized_pnl_pct(
                    pos.realized_pnl, pos.entry_fees_usd, pos.exit_fees_usd,
                    pos.entry_quantity, pos.quantity, pos.entry_price,
                )
            await session.merge(pos)
            log.info("fixed_closed_position", symbol=pos.symbol, id=pos.id,
                     pnl_pct=str(pos.realized_pnl_pct))
        await session.commit()


def _is_residual(quantity: Decimal, price: Decimal) -> bool:
    if not price or quantity <= 0:
        return False
    usd_value = quantity * price
    from backend.trading.position_tracker import DUST_THRESHOLD_USD
    return usd_value >= DUST_THRESHOLD_USD and usd_value < RESIDUAL_THRESHOLD_USD


_residual_notified: set[str] = set()  # cooldown: one notif per symbol per session


async def _notify_residual(symbol: str, quantity: Decimal, price: Decimal, market_type: str):
    usd_value = quantity * price
    log.info("residual_balance_skipped", symbol=symbol, qty=str(quantity),
             usd_value=str(usd_value), market_type=market_type)
    key = f"{symbol}:{market_type}"
    if key in _residual_notified:
        return
    _residual_notified.add(key)
    msg = (
        f"💤 Résidu détecté : {quantity} {symbol.replace('USDC', '')} "
        f"(~${usd_value:.2f}) sur {market_type}\n"
        f"Position non ouverte (< ${RESIDUAL_THRESHOLD_USD})"
    )
    if telegram_notifier.is_positions_enabled():
        asyncio.create_task(telegram_notifier.notify(msg))


async def _reconcile_positions():
    tracker = _tracker()
    stables = settings.stablecoins_set
    ignored = settings.ignored_assets_set

    snapshot_ids = set(tracker._positions.keys())

    prices: dict[str, Decimal] = {}
    try:
        client = await binance_client.get_client()
        for t in await client.get_all_tickers():
            prices[t["symbol"]] = Decimal(t["price"])
    except Exception:
        log.warning("ticker_fetch_failed_for_reconcile", exc_info=True)

    found_keys: set[tuple[str, str]] = set()
    fetched_market_types: set[str] = set()

    # Spot
    try:
        for bal in await binance_client.get_spot_balances():
            asset = bal["asset"]
            if asset in stables or asset in ignored:
                continue
            total = Decimal(bal["free"]) + Decimal(bal["locked"])
            if total <= 0:
                continue
            symbol = tracker._asset_to_symbol(asset)
            price = prices.get(symbol)
            if price and tracker._is_dust(total, price):
                continue
            if price and _is_residual(total, price):
                await _notify_residual(symbol, total, price, "SPOT")
                continue
            found_keys.add((symbol, "SPOT"))
            await _reconcile_single(symbol, "LONG", total, "SPOT")
        fetched_market_types.add("SPOT")
    except Exception:
        log.error("reconcile_spot_failed", exc_info=True)

    # Cross margin
    try:
        for bal in await binance_client.get_cross_margin_balances():
            asset = bal["asset"]
            if asset in stables:
                continue
            free = Decimal(bal.get("free", "0"))
            locked = Decimal(bal.get("locked", "0"))
            borrowed = Decimal(bal.get("borrowed", "0"))
            # Never ignore assets with debt (active SHORT)
            if asset in ignored and borrowed <= 0:
                continue
            symbol = tracker._asset_to_symbol(asset)
            if borrowed > 0:
                found_keys.add((symbol, "CROSS_MARGIN"))
                await _reconcile_single(symbol, "SHORT", borrowed, "CROSS_MARGIN")
            elif free + locked > 0:
                total = free + locked
                price = prices.get(symbol)
                if price and tracker._is_dust(total, price):
                    continue
                if price and _is_residual(total, price):
                    await _notify_residual(symbol, total, price, "CROSS_MARGIN")
                    continue
                found_keys.add((symbol, "CROSS_MARGIN"))
                await _reconcile_single(symbol, "LONG", total, "CROSS_MARGIN")
        fetched_market_types.add("CROSS_MARGIN")
    except Exception:
        log.error("reconcile_cross_margin_failed", exc_info=True)

    # Isolated margin
    try:
        for pair in await binance_client.get_isolated_margin_balances():
            symbol = pair.get("symbol", "")
            base = pair.get("baseAsset", {})
            base_borrowed = Decimal(base.get("borrowed", "0"))
            base_free = Decimal(base.get("free", "0"))
            base_locked = Decimal(base.get("locked", "0"))
            # Never ignore assets with debt (active SHORT)
            if tracker._extract_base_asset(symbol) in ignored and base_borrowed <= 0:
                continue
            if base_borrowed > 0:
                found_keys.add((symbol, "ISOLATED_MARGIN"))
                await _reconcile_single(symbol, "SHORT", base_borrowed, "ISOLATED_MARGIN")
            elif base_free + base_locked > 0:
                total = base_free + base_locked
                price = prices.get(symbol)
                if price and tracker._is_dust(total, price):
                    continue
                if price and _is_residual(total, price):
                    await _notify_residual(symbol, total, price, "ISOLATED_MARGIN")
                    continue
                found_keys.add((symbol, "ISOLATED_MARGIN"))
                await _reconcile_single(symbol, "LONG", total, "ISOLATED_MARGIN")
        fetched_market_types.add("ISOLATED_MARGIN")
    except Exception:
        log.error("reconcile_isolated_margin_failed", exc_info=True)

    # Close stale positions (only those present at snapshot, not added by WS during reconciliation)
    for pos in list(tracker._positions.values()):
        if pos.id not in snapshot_ids:
            continue
        if pos.is_active and (pos.symbol, pos.market_type) not in found_keys:
            if pos.market_type not in fetched_market_types:
                log.warning("skipping_stale_check_fetch_failed",
                            symbol=pos.symbol, market_type=pos.market_type)
                continue
            still_exists = await _verify_position_on_binance(pos)
            if still_exists:
                log.warning("stale_position_still_exists_on_binance",
                            symbol=pos.symbol, market_type=pos.market_type)
                continue
            exit_price, net_pnl, pnl_pct = _get_close_info(pos)
            pos.is_active = False
            pos.quantity = Decimal("0")
            if exit_price:
                pos.exit_price = exit_price
            if net_pnl is not None:
                pos.realized_pnl = net_pnl
                pos.realized_pnl_pct = pnl_pct
            if not pos.closed_at:
                pos.closed_at = tracker._now()
            pos.updated_at = tracker._now()
            await tracker._save_position(pos)
            del tracker._positions[pos.id]
            tracker.clear_pnl_alerts(pos.id)
            log.info("position_closed_stale", symbol=pos.symbol, market_type=pos.market_type,
                     exit_price=str(exit_price), net_pnl=str(net_pnl))
            asyncio.create_task(telegram_notifier.notify_position_closed_reconciled(
                pos.symbol, pos.side, pos.entry_price, exit_price, net_pnl, pnl_pct,
            ))

    for symbol, _ in found_keys:
        await ws_manager.subscribe_symbol(symbol)

    log.info("reconciliation_positions_done", count=len(tracker._positions))


async def _verify_position_on_binance(pos) -> bool:
    tracker = _tracker()
    base_asset = tracker._extract_base_asset(pos.symbol)
    try:
        if pos.market_type == "SPOT":
            for bal in await binance_client.get_spot_balances():
                if bal["asset"] == base_asset:
                    total = Decimal(bal["free"]) + Decimal(bal["locked"])
                    if total > 0:
                        price = pos.current_price or pos.entry_price
                        if not price or not tracker._is_dust(total, price):
                            return True
                    break
        elif pos.market_type == "CROSS_MARGIN":
            for bal in await binance_client.get_cross_margin_balances():
                if bal["asset"] == base_asset:
                    borrowed = Decimal(bal.get("borrowed", "0"))
                    free = Decimal(bal.get("free", "0"))
                    locked = Decimal(bal.get("locked", "0"))
                    if borrowed > 0 or free > 0 or locked > 0:
                        return True
                    break
        elif pos.market_type == "ISOLATED_MARGIN":
            for pair in await binance_client.get_isolated_margin_balances():
                if pair.get("symbol") == pos.symbol:
                    base = pair.get("baseAsset", {})
                    borrowed = Decimal(base.get("borrowed", "0"))
                    free = Decimal(base.get("free", "0"))
                    locked = Decimal(base.get("locked", "0"))
                    if borrowed > 0 or free > 0 or locked > 0:
                        return True
                    break
    except Exception:
        log.warning("verify_position_on_binance_failed", symbol=pos.symbol,
                    market_type=pos.market_type, exc_info=True)
        return True  # assume still exists if verification fails
    return False


def _get_close_info(pos) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if pos.exit_price and pos.exit_price > 0 and pos.realized_pnl is not None:
        net = pnl.net_realized_pnl(
            pos.realized_pnl, pos.entry_fees_usd or Decimal("0"),
            pos.exit_fees_usd or Decimal("0"),
        )
        return pos.exit_price, net, pos.realized_pnl_pct
    return None, None, None


async def _reconcile_single(
    symbol: str, side: str, binance_qty: Decimal, market_type: str,
):
    tracker = _tracker()
    await _backfill_trades_for_symbol(symbol, market_type)

    existing = tracker._find_position(symbol, market_type)
    if existing:
        changes = {}
        qty_diff = abs(existing.quantity - binance_qty)
        if qty_diff > 0 and qty_diff / max(existing.quantity, Decimal("1")) > Decimal("0.001"):
            changes["quantity"] = binance_qty
            log.warning("reconcile_qty_mismatch", symbol=symbol,
                        db_qty=str(existing.quantity), binance_qty=str(binance_qty))
        if existing.side != side:
            changes["side"] = side
            log.warning("reconcile_side_mismatch", symbol=symbol,
                        db_side=existing.side, binance_side=side)
        if not existing.entry_price or existing.entry_price <= 0:
            entry_price, fees = await _calculate_entry_price(symbol, side, binance_qty, market_type)
            changes["entry_price"] = entry_price
            changes["entry_fees_usd"] = fees
        elif not existing.entry_fees_usd or existing.entry_fees_usd <= 0:
            _, fees = await _calculate_entry_price(symbol, side, binance_qty, market_type)
            changes["entry_fees_usd"] = fees
        if changes:
            for k, v in changes.items():
                setattr(existing, k, v)
            existing.updated_at = tracker._now()
            await tracker._save_position(existing)
            log.info("position_reconciled", symbol=symbol, changes=list(changes.keys()))
    else:
        entry_price, fees = await _calculate_entry_price(symbol, side, binance_qty, market_type)
        pos = Position(
            symbol=symbol, side=side, entry_price=entry_price,
            quantity=binance_qty, entry_quantity=binance_qty,
            market_type=market_type, entry_fees_usd=fees,
            opened_at=tracker._now(), is_active=True,
        )
        async with async_session() as session:
            session.add(pos)
            await session.commit()
            await session.refresh(pos)
        tracker._positions[pos.id] = pos
        await ws_manager.subscribe_symbol(symbol)
        log.info("position_discovered", symbol=symbol, side=side,
                 entry=str(entry_price), qty=str(binance_qty))


# --- Entry price calculation ---


async def _calculate_entry_price(
    symbol: str, side: str, quantity: Decimal, market_type: str
) -> tuple[Decimal, Decimal]:
    tracker = _tracker()
    try:
        if market_type == "SPOT":
            trades = await binance_client.get_my_trades(symbol)
        else:
            trades = await binance_client.get_margin_trades(
                symbol, is_isolated=(market_type == "ISOLATED_MARGIN")
            )
    except Exception:
        log.warning("entry_price_fetch_failed", symbol=symbol)
        return Decimal("0"), Decimal("0")

    if not trades:
        return Decimal("0"), Decimal("0")

    trades.sort(key=lambda t: t["time"], reverse=True)
    target_buyer = side == "LONG"
    base_asset = tracker._extract_base_asset(symbol)

    accumulated = Decimal("0")
    weighted = Decimal("0")
    total_fees = Decimal("0")

    for t in trades:
        if t["isBuyer"] != target_buyer:
            continue
        qty = Decimal(t["qty"])
        price = Decimal(t["price"])
        comm = Decimal(t.get("commission", "0"))
        comm_asset = t.get("commissionAsset", "")
        if comm_asset == base_asset and target_buyer:
            qty -= comm

        remaining = quantity - accumulated
        used = min(qty, remaining)
        weighted += used * price
        accumulated += used
        total_fees += await tracker._commission_to_usd(comm, comm_asset, price, symbol)
        if accumulated >= quantity:
            break

    if accumulated <= 0:
        return Decimal("0"), Decimal("0")
    return weighted / accumulated, total_fees


# --- Trade backfill ---


async def _backfill_trades_for_symbol(symbol: str, market_type: str):
    tracker = _tracker()
    try:
        if market_type == "SPOT":
            trades = await binance_client.get_my_trades(symbol)
        else:
            trades = await binance_client.get_margin_trades(
                symbol, is_isolated=(market_type == "ISOLATED_MARGIN")
            )
    except Exception:
        log.warning("backfill_trades_fetch_failed", symbol=symbol)
        return
    if not trades:
        return
    all_ids = [str(t.get("id", "")) for t in trades]
    async with async_session() as session:
        result = await session.execute(
            select(Trade.binance_trade_id).where(Trade.binance_trade_id.in_(all_ids))
        )
        existing_ids = set(result.scalars().all())
        added = 0
        for t in trades:
            trade_id = str(t.get("id", ""))
            if trade_id in existing_ids:
                continue
            qty = Decimal(t["qty"])
            price = Decimal(t["price"])
            trade = Trade(
                binance_trade_id=trade_id,
                binance_order_id=str(t.get("orderId", "")),
                symbol=symbol,
                side="BUY" if t["isBuyer"] else "SELL",
                price=price,
                quantity=qty,
                quote_qty=price * qty,
                commission=Decimal(t.get("commission", "0")),
                commission_asset=t.get("commissionAsset", ""),
                market_type=market_type,
                is_maker=t.get("isMaker", False),
                executed_at=datetime.fromtimestamp(t["time"] / 1000, tz=timezone.utc),
            )
            session.add(trade)
            added += 1
        await session.commit()
    log.info("trades_backfilled", symbol=symbol, total=len(trades), added=added)


async def _backfill_all_trades():
    async with async_session() as session:
        from sqlalchemy import distinct
        result = await session.execute(
            select(distinct(Position.symbol), Position.market_type)
        )
        pairs = [(s, mt) for s, mt in result.all()]

    known_symbols = {s for s, _ in pairs}
    for symbol in settings.watchlist:
        if symbol not in known_symbols:
            pairs.append((symbol, "CROSS_MARGIN"))

    already_done: set[str] = set()
    for symbol, market_type in pairs:
        if symbol in already_done:
            continue
        already_done.add(symbol)
        await _backfill_trades_for_symbol(symbol, market_type)
    log.info("backfill_all_trades_done", symbols=len(already_done))


# --- Order ref verification ---


async def _verify_order_refs():
    tracker = _tracker()
    for pos in list(tracker._positions.values()):
        try:
            if pos.market_type == "SPOT":
                open_orders = await binance_client.get_open_orders(pos.symbol)
            else:
                open_orders = await binance_client.get_margin_open_orders(
                    pos.symbol, is_isolated=(pos.market_type == "ISOLATED_MARGIN"),
                )
        except Exception:
            log.warning("verify_orders_failed", symbol=pos.symbol)
            continue

        open_ids = {str(o["orderId"]) for o in open_orders}
        open_list_ids = {str(o.get("orderListId", "")) for o in open_orders} - {"", "-1"}

        log.info("verify_order_refs", symbol=pos.symbol,
                 pos_sl=pos.sl_order_id, pos_tp=pos.tp_order_id,
                 pos_oco=pos.oco_order_list_id,
                 open_order_ids=list(open_ids), open_list_ids=list(open_list_ids))

        updates = {}

        # Clear stale refs
        if pos.sl_order_id and pos.sl_order_id not in open_ids:
            updates["sl_order_id"] = None
        if pos.tp_order_id and pos.tp_order_id not in open_ids:
            updates["tp_order_id"] = None
        if pos.oco_order_list_id and pos.oco_order_list_id not in open_list_ids:
            # Margin OCO: Binance returns orderListId=-1 for individual orders
            # Preserve OCO ref if both SL+TP close-side orders still exist
            close_side = "SELL" if pos.side == "LONG" else "BUY"
            close_orders = [o for o in open_orders if o.get("side") == close_side]
            has_sl = any(o.get("type") == "STOP_LOSS_LIMIT" for o in close_orders)
            has_tp = any(o.get("type") in ("TAKE_PROFIT_LIMIT", "LIMIT_MAKER")
                         for o in close_orders)
            if has_sl and has_tp:
                log.info("oco_preserved_margin", symbol=pos.symbol,
                         oco=pos.oco_order_list_id)
            else:
                updates["oco_order_list_id"] = None

        # Effective values after stale clearing
        eff_sl = updates.get("sl_order_id", pos.sl_order_id)
        eff_tp = updates.get("tp_order_id", pos.tp_order_id)
        eff_oco = updates.get("oco_order_list_id", pos.oco_order_list_id)

        # Discover open orders not yet tracked
        if open_list_ids and not eff_oco:
            oco_id = next(iter(open_list_ids))
            updates["oco_order_list_id"] = oco_id
            updates["sl_order_id"] = None
            updates["tp_order_id"] = None
            log.info("oco_discovered", symbol=pos.symbol, order_list_id=oco_id)
        elif not eff_oco:
            close_side = "SELL" if pos.side == "LONG" else "BUY"
            for o in open_orders:
                oid = str(o["orderId"])
                oside = o.get("side", "")
                otype = o.get("type", "")
                if oside != close_side:
                    continue
                if otype == "STOP_LOSS_LIMIT" and not eff_sl:
                    updates["sl_order_id"] = oid
                    eff_sl = oid
                    log.info("sl_discovered", symbol=pos.symbol, order_id=oid)
                    await order_manager.ensure_order_record(pos, o, "SL")
                elif otype in ("TAKE_PROFIT_LIMIT", "LIMIT_MAKER") and not eff_tp:
                    updates["tp_order_id"] = oid
                    eff_tp = oid
                    log.info("tp_discovered", symbol=pos.symbol, order_id=oid)
                    await order_manager.ensure_order_record(pos, o, "TP")

        # Ensure OCO order record exists in DB (covers crash recovery)
        final_oco = updates.get("oco_order_list_id", pos.oco_order_list_id)
        if final_oco and final_oco in open_list_ids:
            await order_manager.ensure_oco_order_record(pos, final_oco, open_orders)

        # Ensure Order records exist for all tracked orders (covers crash recovery)
        final_sl = updates.get("sl_order_id", pos.sl_order_id)
        final_tp = updates.get("tp_order_id", pos.tp_order_id)
        for o in open_orders:
            oid = str(o["orderId"])
            if oid == final_sl:
                await order_manager.ensure_order_record(pos, o, "SL")
            elif oid == final_tp:
                await order_manager.ensure_order_record(pos, o, "TP")

        # Clean stale Order records (cancelled on Binance but still NEW in DB)
        await order_manager.cleanup_stale_orders(pos.id, open_ids)

        if updates:
            for k, v in updates.items():
                setattr(pos, k, v)
            await tracker._save_position(pos)
            log.info("order_refs_updated", symbol=pos.symbol, updates={k: v for k, v in updates.items()})
