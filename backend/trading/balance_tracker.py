import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select, func, and_

from backend.exchange import binance_client, ws_manager
from backend.trading import position_tracker
from backend.core.config import settings
from backend.core.database import async_session
from backend.core.models import Balance, Price
from backend.exchange.ws_manager import EVENT_ACCOUNT_UPDATE

log = structlog.get_logger()

_snapshot_task: asyncio.Task | None = None
_debounce_task: asyncio.Task | None = None
_DEBOUNCE_DELAY = 3


async def start():
    ws_manager.on(EVENT_ACCOUNT_UPDATE, _handle_account_update)
    global _snapshot_task
    _snapshot_task = asyncio.create_task(_run_periodic_snapshot())
    log.info("balance_tracker_started")


async def stop():
    for task in (_snapshot_task, _debounce_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    log.info("balance_tracker_stopped")


# --- Periodic full snapshot ---


async def _run_periodic_snapshot():
    while True:
        try:
            await _take_full_snapshot()
            await asyncio.sleep(settings.balance_snapshot_interval)
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("balance_snapshot_failed", exc_info=True)
            await asyncio.sleep(30)


async def _take_full_snapshot():
    now = datetime.now(timezone.utc)
    records: list[Balance] = []

    # Spot
    try:
        for bal in await binance_client.get_spot_balances():
            free = Decimal(bal["free"])
            locked = Decimal(bal["locked"])
            if free + locked <= 0:
                continue
            records.append(Balance(
                asset=bal["asset"],
                free=free,
                locked=locked,
                net=free + locked,
                wallet_type="SPOT",
                snapshot_at=now,
            ))
    except Exception:
        log.error("snapshot_spot_failed", exc_info=True)

    # Cross margin
    try:
        for bal in await binance_client.get_cross_margin_balances():
            free = Decimal(bal.get("free", "0"))
            locked = Decimal(bal.get("locked", "0"))
            borrowed = Decimal(bal.get("borrowed", "0"))
            interest = Decimal(bal.get("interest", "0"))
            net = free + locked - borrowed - interest
            if free + locked + borrowed <= 0:
                continue
            records.append(Balance(
                asset=bal["asset"],
                free=free,
                locked=locked,
                borrowed=borrowed,
                interest=interest,
                net=net,
                wallet_type="CROSS_MARGIN",
                snapshot_at=now,
            ))
    except Exception:
        log.error("snapshot_cross_margin_failed", exc_info=True)

    # Isolated margin
    try:
        for pair in await binance_client.get_isolated_margin_balances():
            for key in ("baseAsset", "quoteAsset"):
                a = pair.get(key, {})
                free = Decimal(a.get("free", "0"))
                locked = Decimal(a.get("locked", "0"))
                borrowed = Decimal(a.get("borrowed", "0"))
                interest = Decimal(a.get("interest", "0"))
                net = free + locked - borrowed - interest
                if free + locked + borrowed <= 0:
                    continue
                records.append(Balance(
                    asset=a.get("asset", ""),
                    free=free,
                    locked=locked,
                    borrowed=borrowed,
                    interest=interest,
                    net=net,
                    wallet_type="ISOLATED_MARGIN",
                    snapshot_at=now,
                ))
    except Exception:
        log.error("snapshot_isolated_margin_failed", exc_info=True)

    if records:
        await _fill_usd_values(records)
        async with async_session() as session:
            session.add_all(records)
            await session.commit()

    log.info("balance_snapshot_complete", count=len(records))


# --- USD value computation ---


async def _fill_usd_values(records: list[Balance]):
    stables = settings.stablecoins_set
    # Build price map from active positions (in-memory, free)
    price_map: dict[str, Decimal] = {}
    for pos in position_tracker.get_positions():
        if pos.current_price and pos.current_price > 0:
            # Extract base asset from symbol (e.g. "BTCUSDC" -> "BTC")
            for stable in stables:
                if pos.symbol.endswith(stable):
                    base = pos.symbol[: -len(stable)]
                    price_map[base] = pos.current_price
                    break

    # Assets without a price from positions: fallback to latest DB price
    missing = {r.asset for r in records if r.asset not in stables and r.asset not in price_map}
    if missing:
        # Build all candidate symbols: assetUSDC and assetUSDT for each missing asset
        candidate_symbols = []
        symbol_to_asset: dict[str, str] = {}
        for asset in missing:
            for stable in ("USDC", "USDT"):
                sym = f"{asset}{stable}"
                candidate_symbols.append(sym)
                symbol_to_asset[sym] = asset

        # Single query: latest price per symbol using MAX(recorded_at)
        latest_sub = (
            select(Price.symbol, func.max(Price.recorded_at).label("max_at"))
            .where(Price.symbol.in_(candidate_symbols))
            .group_by(Price.symbol)
            .subquery()
        )
        async with async_session() as session:
            result = await session.execute(
                select(Price.symbol, Price.price).join(
                    latest_sub,
                    and_(
                        Price.symbol == latest_sub.c.symbol,
                        Price.recorded_at == latest_sub.c.max_at,
                    ),
                )
            )
            # Prefer USDC over USDT: sort so USDC symbols are processed first
            rows = sorted(result.all(), key=lambda r: 0 if r[0].endswith("USDC") else 1)
            for sym, price in rows:
                asset = symbol_to_asset[sym]
                if price and price > 0 and asset not in price_map:
                    price_map[asset] = price

    for record in records:
        if record.asset in stables:
            record.usd_value = record.net
        elif record.asset in price_map:
            record.usd_value = record.net * price_map[record.asset]


# --- Event-driven snapshot ---


async def _debounced_snapshot():
    await asyncio.sleep(_DEBOUNCE_DELAY)
    try:
        await _take_full_snapshot()
    except Exception:
        log.error("event_triggered_snapshot_failed", exc_info=True)


async def _handle_account_update(msg: dict):
    balances = msg.get("B", [])
    if not balances:
        return
    assets = [b.get("a", "") for b in balances]
    log.info("balance_event_received", assets=assets)
    global _debounce_task
    if _debounce_task and not _debounce_task.done():
        _debounce_task.cancel()
    _debounce_task = asyncio.create_task(_debounced_snapshot())
