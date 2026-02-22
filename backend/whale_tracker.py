import asyncio
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from backend import binance_client
from backend.config import settings

log = structlog.get_logger()

MAX_ALERTS = 50

_whale_alerts: deque = deque(maxlen=MAX_ALERTS)
_poll_task: asyncio.Task | None = None


async def start():
    global _poll_task
    _poll_task = asyncio.create_task(_run_poll())
    log.info("whale_tracker_started")


async def stop():
    if _poll_task:
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
    log.info("whale_tracker_stopped")


def get_whale_alerts() -> list[dict]:
    return list(_whale_alerts)


async def _run_poll():
    # Small initial delay to let other services initialize
    await asyncio.sleep(10)
    while True:
        try:
            await _poll_agg_trades()
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("whale_poll_failed", exc_info=True)
        await asyncio.sleep(settings.whale_poll_interval)


async def _poll_agg_trades():
    client = await binance_client.get_client()
    symbols = settings.watchlist
    min_qty = Decimal(str(settings.whale_min_quote_qty))

    for symbol in symbols:
        try:
            trades = await client.get_aggregate_trades(symbol=symbol, limit=100)
            for t in trades:
                price = Decimal(t["p"])
                qty = Decimal(t["q"])
                quote_qty = price * qty

                if quote_qty < min_qty:
                    continue

                # Deduplicate by trade ID
                trade_id = t["a"]
                if any(a.get("trade_id") == trade_id for a in _whale_alerts):
                    continue

                side = "SELL" if t["m"] else "BUY"  # m = isBuyerMaker
                ts = datetime.fromtimestamp(t["T"] / 1000, tz=timezone.utc)

                alert = {
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "side": side,
                    "price": str(price),
                    "quantity": str(qty),
                    "quote_qty": str(round(quote_qty, 2)),
                    "timestamp": ts.isoformat(),
                }
                _whale_alerts.appendleft(alert)
                log.info("whale_detected", symbol=symbol, side=side,
                         quote_qty=str(round(quote_qty, 0)))
        except Exception:
            log.warning("whale_poll_symbol_failed", symbol=symbol, exc_info=True)
