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
BACKFILL_MAX_PAGES = 20
BACKFILL_LOOKBACK_MS = 4 * 3600 * 1000
USDT_THRESHOLD_MULTIPLIER = Decimal("2")

_whale_alerts: deque = deque(maxlen=MAX_ALERTS)
_stream_task: asyncio.Task | None = None


def _threshold_for(symbol: str, base_min: Decimal) -> Decimal:
    return base_min * USDT_THRESHOLD_MULTIPLIER if symbol.upper().endswith("USDT") else base_min


async def _backfill_symbol(client, symbol, min_qty, cutoff_ms):
    threshold = _threshold_for(symbol, min_qty)
    found = []
    seen_ids = set()
    oldest_id = None

    for _ in range(BACKFILL_MAX_PAGES):
        kwargs = {"symbol": symbol, "limit": BACKFILL_LIMIT}
        if oldest_id is not None:
            kwargs["fromId"] = max(1, oldest_id - BACKFILL_LIMIT)

        trades = await client.get_aggregate_trades(**kwargs)
        if not trades:
            break

        for t in trades:
            tid = t["a"]
            if tid in seen_ids or t["T"] < cutoff_ms:
                continue
            seen_ids.add(tid)
            price = Decimal(t["p"])
            qty = Decimal(t["q"])
            quote_qty = price * qty
            if quote_qty < threshold:
                continue
            side = "SELL" if t["m"] else "BUY"
            ts = datetime.fromtimestamp(t["T"] / 1000, tz=timezone.utc)
            found.append({
                "trade_id": tid,
                "symbol": symbol.upper(),
                "side": side,
                "price": str(price),
                "quantity": str(qty),
                "quote_qty": str(round(quote_qty, 2)),
                "timestamp": ts.isoformat(),
                "_ts": t["T"],
            })

        new_oldest = trades[0]["a"]
        if new_oldest == oldest_id:
            break
        oldest_id = new_oldest

        if trades[0]["T"] <= cutoff_ms:
            break

    return found


def _whale_symbols() -> list[str]:
    base = settings.watchlist
    extra = []
    for s in base:
        usdt = s.replace("USDC", "USDT")
        if usdt != s and usdt not in base:
            extra.append(usdt)
    return base + extra


async def _backfill():
    min_qty = Decimal(str(settings.whale_min_quote_qty))
    symbols = _whale_symbols()
    if not symbols:
        return

    client = await get_client()
    cutoff_ms = int(time.time() * 1000) - BACKFILL_LOOKBACK_MS

    results = await asyncio.gather(
        *[_backfill_symbol(client, s, min_qty, cutoff_ms) for s in symbols],
        return_exceptions=True,
    )

    found = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            log.warning("whale_backfill_error", symbol=symbols[i], error=str(r))
        else:
            found.extend(r)

    found.sort(key=lambda x: x["_ts"])
    for alert in found:
        del alert["_ts"]
        _whale_alerts.appendleft(alert)

    if found:
        log.info("whale_backfill_done", count=len(found))


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
            symbols = _whale_symbols()
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

                    if quote_qty < _threshold_for(data["s"], min_qty):
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
