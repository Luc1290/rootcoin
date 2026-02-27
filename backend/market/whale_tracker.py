import asyncio
import json
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal

import structlog
import websockets

from backend.core.config import settings
from backend.exchange.binance_client import get_client

log = structlog.get_logger()

BINANCE_WS_URL = "wss://stream.binance.com:9443"
MAX_ALERTS = 50
MAX_BACKOFF = 60
STABLE_CONNECTION_RESET = 300
BACKFILL_LIMIT = 1000

_whale_alerts: deque = deque(maxlen=MAX_ALERTS)
_stream_task: asyncio.Task | None = None


async def _backfill():
    """Fetch recent aggTrades via REST to recover whale alerts after restart."""
    min_qty = Decimal(str(settings.whale_min_quote_qty))
    symbols = settings.watchlist
    if not symbols:
        return

    client = await get_client()
    found = []

    for symbol in symbols:
        try:
            trades = await client.get_aggregate_trades(
                symbol=symbol, limit=BACKFILL_LIMIT,
            )
            for t in trades:
                price = Decimal(t["p"])
                qty = Decimal(t["q"])
                quote_qty = price * qty
                if quote_qty < min_qty:
                    continue
                side = "SELL" if t["m"] else "BUY"
                ts = datetime.fromtimestamp(t["T"] / 1000, tz=timezone.utc)
                found.append({
                    "trade_id": t["a"],
                    "symbol": symbol.upper(),
                    "side": side,
                    "price": str(price),
                    "quantity": str(qty),
                    "quote_qty": str(round(quote_qty, 2)),
                    "timestamp": ts.isoformat(),
                    "_ts": t["T"],
                })
        except Exception:
            log.warning("whale_backfill_error", symbol=symbol, exc_info=True)

    found.sort(key=lambda x: x["_ts"])
    for alert in found:
        del alert["_ts"]
        _whale_alerts.appendleft(alert)

    if found:
        log.info("whale_backfill_done", count=len(found), symbols=symbols)


async def start():
    global _stream_task
    await _backfill()
    _stream_task = asyncio.create_task(_run_stream())
    log.info("whale_tracker_started")


async def stop():
    if _stream_task:
        _stream_task.cancel()
        try:
            await _stream_task
        except asyncio.CancelledError:
            pass
    log.info("whale_tracker_stopped")


def get_whale_alerts() -> list[dict]:
    return list(_whale_alerts)


async def _run_stream():
    backoff = 1
    min_qty = Decimal(str(settings.whale_min_quote_qty))

    while True:
        try:
            symbols = settings.watchlist
            if not symbols:
                await asyncio.sleep(5)
                continue

            streams = [f"{s.lower()}@aggTrade" for s in symbols]
            url = f"{BINANCE_WS_URL}/stream?streams={'/'.join(streams)}"

            async with websockets.connect(
                url, ping_interval=30, ping_timeout=60,
            ) as ws:
                connected_at = time.monotonic()
                backoff = 1
                log.info("whale_stream_connected", symbols=symbols)

                async for raw in ws:
                    msg = json.loads(raw)
                    data = msg.get("data", {})
                    if data.get("e") != "aggTrade":
                        continue

                    price = Decimal(data["p"])
                    qty = Decimal(data["q"])
                    quote_qty = price * qty

                    if quote_qty < min_qty:
                        continue

                    trade_id = data["a"]
                    if any(a.get("trade_id") == trade_id for a in _whale_alerts):
                        continue

                    side = "SELL" if data["m"] else "BUY"
                    ts = datetime.fromtimestamp(data["T"] / 1000, tz=timezone.utc)

                    _whale_alerts.appendleft({
                        "trade_id": trade_id,
                        "symbol": data["s"],
                        "side": side,
                        "price": str(price),
                        "quantity": str(qty),
                        "quote_qty": str(round(quote_qty, 2)),
                        "timestamp": ts.isoformat(),
                    })
                    log.info("whale_detected", symbol=data["s"], side=side,
                             quote_qty=str(round(quote_qty, 0)))

                    if (time.monotonic() - connected_at) > STABLE_CONNECTION_RESET:
                        backoff = 1
                        connected_at = time.monotonic()

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("whale_stream_disconnected", error=str(e), reconnect_in=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
