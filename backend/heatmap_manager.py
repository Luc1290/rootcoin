import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import aiohttp
import structlog

from backend import binance_client
from backend.config import settings

log = structlog.get_logger()

STALE_THRESHOLD = 900
EXCLUDED_SUFFIXES = ("UP", "DOWN", "BEAR", "BULL")
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker"
VALID_WINDOWS = ("15m", "1h", "4h")

_heatmap_cache: dict[str, dict] = {}
_active_window: str = "4h"
_refresh_task: asyncio.Task | None = None


async def start():
    global _refresh_task
    _refresh_task = asyncio.create_task(_run_refresh())
    log.info("heatmap_manager_started")


async def stop():
    if _refresh_task:
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
    log.info("heatmap_manager_stopped")


async def ensure_window_data(window: str):
    global _active_window
    if window not in VALID_WINDOWS:
        window = "4h"
    _active_window = window
    cache = _heatmap_cache.get(window, {})
    fetched = cache.get("fetched_at")
    needs_fetch = not fetched
    if fetched:
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        needs_fetch = age > STALE_THRESHOLD
    if needs_fetch:
        try:
            await _fetch_tickers(window)
        except Exception:
            log.error("heatmap_ensure_fetch_failed", window=window, exc_info=True)


def get_heatmap_data(limit: int | None = None, window: str = "4h") -> dict:
    if window not in VALID_WINDOWS:
        window = "4h"
    top_n = limit or settings.heatmap_top_n
    cache = _heatmap_cache.get(window, {})
    assets = cache.get("assets", [])[:top_n]
    fetched = cache.get("fetched_at")
    is_stale = True
    if fetched:
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        is_stale = age > STALE_THRESHOLD
    return {
        "assets": assets,
        "updated_at": fetched.isoformat() if fetched else None,
        "is_stale": is_stale,
        "window": window,
    }


async def _run_refresh():
    while True:
        try:
            await _fetch_tickers(_active_window)
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("heatmap_fetch_failed", exc_info=True)
        await asyncio.sleep(settings.heatmap_refresh_interval)


async def _fetch_tickers(window: str = "4h"):
    # Step 1: get 24h tickers to identify top symbols by volume
    client = await binance_client.get_client()
    tickers_24h = await client.get_ticker()
    stables = settings.stablecoins_set

    candidates = []
    for t in tickers_24h:
        symbol = t["symbol"]
        if not symbol.endswith("USDC"):
            continue
        base = symbol[:-4]
        if base in stables:
            continue
        if any(base.endswith(s) for s in EXCLUDED_SUFFIXES):
            continue
        try:
            volume = Decimal(t["quoteVolume"])
        except Exception:
            continue
        candidates.append({"symbol": symbol, "base_asset": base, "volume": volume})

    # Sort by volume, keep top N
    candidates.sort(key=lambda a: a["volume"], reverse=True)
    top = candidates[:settings.heatmap_top_n]
    if not top:
        return

    # Step 2: fetch rolling window for these symbols
    symbols_list = [a["symbol"] for a in top]
    # Binance requires symbols as raw JSON array in the URL (not percent-encoded by aiohttp)
    symbols_param = "[" + ",".join(f'"{s}"' for s in symbols_list) + "]"
    url = f"{BINANCE_TICKER_URL}?windowSize={window}&symbols={symbols_param}"

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                log.warning("heatmap_4h_api_error", status=resp.status)
                return
            tickers_window = await resp.json()

    # Index window data by symbol
    change_map = {}
    for t in tickers_window:
        change_map[t["symbol"]] = {
            "change": str(round(Decimal(t["priceChangePercent"]), 2)),
            "price": t["lastPrice"],
        }

    # Build final list
    assets = []
    for a in top:
        data = change_map.get(a["symbol"])
        if not data:
            continue
        assets.append({
            "symbol": a["symbol"],
            "base_asset": a["base_asset"],
            "price": data["price"],
            "change_24h": data["change"],
            "volume_24h": str(round(a["volume"], 0)),
        })

    _heatmap_cache[window] = {
        "assets": assets,
        "fetched_at": datetime.now(timezone.utc),
    }
    log.info("heatmap_refreshed", window=window, count=len(assets))
