import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select

from backend import binance_client, ws_manager, position_tracker
from backend.config import settings
from backend.database import async_session
from backend.models import Balance, Price
from backend.ws_manager import EVENT_ACCOUNT_UPDATE

log = structlog.get_logger()

_snapshot_task: asyncio.Task | None = None


async def start():
    ws_manager.on(EVENT_ACCOUNT_UPDATE, _handle_account_update)
    global _snapshot_task
    _snapshot_task = asyncio.create_task(_run_periodic_snapshot())
    log.info("balance_tracker_started")


async def stop():
    if _snapshot_task:
        _snapshot_task.cancel()
        try:
            await _snapshot_task
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
        async with async_session() as session:
            for asset in missing:
                for stable in ("USDC", "USDT"):
                    symbol = f"{asset}{stable}"
                    result = await session.execute(
                        select(Price.price)
                        .where(Price.symbol == symbol)
                        .order_by(Price.recorded_at.desc())
                        .limit(1)
                    )
                    price = result.scalar_one_or_none()
                    if price and price > 0:
                        price_map[asset] = price
                        break

    for record in records:
        if record.asset in stables:
            record.usd_value = record.net
        elif record.asset in price_map:
            record.usd_value = record.net * price_map[record.asset]


# --- Event-driven snapshot ---


async def _handle_account_update(msg: dict):
    balances = msg.get("B", [])
    if not balances:
        return

    now = datetime.now(timezone.utc)
    records: list[Balance] = []

    for b in balances:
        asset = b.get("a", "")
        free = Decimal(b.get("f", "0"))
        locked = Decimal(b.get("l", "0"))
        if not asset:
            continue
        records.append(Balance(
            asset=asset,
            free=free,
            locked=locked,
            net=free + locked,
            wallet_type="SPOT",
            snapshot_at=now,
        ))

    if records:
        async with async_session() as session:
            session.add_all(records)
            await session.commit()
        log.debug("balance_event_recorded", assets=[r.asset for r in records])
