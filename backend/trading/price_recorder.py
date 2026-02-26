import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog
from sqlalchemy import delete

from backend.exchange import ws_manager
from backend.core.config import settings
from backend.core.database import async_session
from backend.core.models import Price
from backend.exchange.ws_manager import EVENT_PRICE_UPDATE

log = structlog.get_logger()

CLEANUP_INTERVAL = 3600

_last_recorded: dict[str, float] = {}
_cleanup_task: asyncio.Task | None = None


async def start():
    ws_manager.on(EVENT_PRICE_UPDATE, _handle_price)
    global _cleanup_task
    _cleanup_task = asyncio.create_task(_run_cleanup())
    log.info("price_recorder_started")


async def stop():
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
    log.info("price_recorder_stopped")


async def _handle_price(msg: dict):
    symbol = msg.get("s", "")
    price_str = msg.get("c", "0")
    price = Decimal(price_str)
    if not symbol or price <= 0:
        return

    now = time.monotonic()
    last = _last_recorded.get(symbol, 0)
    if now - last < settings.price_record_interval:
        return

    _last_recorded[symbol] = now

    record = Price(
        symbol=symbol,
        price=price,
        source="ticker",
        recorded_at=datetime.now(timezone.utc),
    )
    async with async_session() as session:
        session.add(record)
        await session.commit()

    log.debug("price_recorded", symbol=symbol, price=price_str)


async def _run_cleanup():
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)
            cutoff = datetime.now(timezone.utc) - timedelta(days=settings.price_retention_days)
            async with async_session() as session:
                result = await session.execute(
                    delete(Price).where(Price.recorded_at < cutoff)
                )
                await session.commit()
                deleted = result.rowcount
            if deleted:
                log.info("prices_cleaned", deleted=deleted, older_than=str(cutoff))
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("price_cleanup_failed", exc_info=True)
