import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from backend import binance_client, position_tracker
from backend.config import settings

log = structlog.get_logger()

_depth_cache: dict[str, dict] = {}
_cache_time: datetime | None = None
_poll_task: asyncio.Task | None = None


async def start():
    global _poll_task
    _poll_task = asyncio.create_task(_run_poll())
    log.info("orderbook_tracker_started")


async def stop():
    if _poll_task:
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
    log.info("orderbook_tracker_stopped")


def get_orderbook_data(symbol: str | None = None) -> dict:
    if symbol:
        return _depth_cache.get(symbol, {})
    return {
        "orderbooks": dict(_depth_cache),
        "updated_at": _cache_time.isoformat() if _cache_time else None,
    }


def get_imbalance(symbol: str) -> float | None:
    data = _depth_cache.get(symbol)
    if not data:
        return None
    return data.get("imbalance")


# ── Polling loop ──────────────────────────────────────────────

async def _run_poll():
    await asyncio.sleep(12)
    while True:
        try:
            await _fetch_all_depths()
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("orderbook_poll_failed", exc_info=True)
        await asyncio.sleep(settings.orderbook_poll_interval)


def _get_symbols() -> list[str]:
    symbols = set(settings.watchlist)
    for pos in position_tracker.get_positions():
        symbols.add(pos.symbol)
    return sorted(symbols)


async def _fetch_all_depths():
    global _cache_time
    client = await binance_client.get_client()
    symbols = _get_symbols()

    for symbol in symbols:
        try:
            raw = await client.get_order_book(
                symbol=symbol,
                limit=settings.orderbook_depth_limit,
            )
            _depth_cache[symbol] = _analyze_depth(symbol, raw)
        except Exception:
            log.warning("orderbook_fetch_failed", symbol=symbol, exc_info=True)

    _cache_time = datetime.now(timezone.utc)


# ── Analysis ──────────────────────────────────────────────────

def _analyze_depth(symbol: str, raw: dict) -> dict:
    bids = [(Decimal(p), Decimal(q)) for p, q in raw.get("bids", [])]
    asks = [(Decimal(p), Decimal(q)) for p, q in raw.get("asks", [])]

    empty = {
        "symbol": symbol, "imbalance": 0, "walls": [],
        "spread": "0", "spread_pct": "0",
        "total_bid_vol": "0", "total_ask_vol": "0",
        "depth_bands": {}, "bids": [], "asks": [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not bids or not asks:
        return empty

    best_bid = bids[0][0]
    best_ask = asks[0][0]
    mid_price = (best_bid + best_ask) / 2
    spread = best_ask - best_bid

    total_bid_vol = sum(q for _, q in bids)
    total_ask_vol = sum(q for _, q in asks)
    total_vol = total_bid_vol + total_ask_vol

    imbalance = 0.0
    if total_vol > 0:
        imbalance = float((total_bid_vol - total_ask_vol) / total_vol)

    walls = _detect_walls(bids, asks, total_vol, mid_price)

    depth_bands = {}
    for pct in (Decimal("0.005"), Decimal("0.01"), Decimal("0.02")):
        lower = mid_price * (1 - pct)
        upper = mid_price * (1 + pct)
        bid_in_band = sum(q for p, q in bids if p >= lower)
        ask_in_band = sum(q for p, q in asks if p <= upper)
        band_total = bid_in_band + ask_in_band
        depth_bands[str(float(pct * 100))] = {
            "bid_vol": str(bid_in_band),
            "ask_vol": str(ask_in_band),
            "imbalance": round(float((bid_in_band - ask_in_band) / band_total), 3) if band_total > 0 else 0,
        }

    top_bids = [{"price": str(p), "quantity": str(q)} for p, q in bids[:10]]
    top_asks = [{"price": str(p), "quantity": str(q)} for p, q in asks[:10]]

    return {
        "symbol": symbol,
        "imbalance": round(imbalance, 3),
        "spread": str(spread),
        "spread_pct": str(round(spread / mid_price * 100, 4)) if mid_price else "0",
        "total_bid_vol": str(total_bid_vol),
        "total_ask_vol": str(total_ask_vol),
        "walls": walls,
        "depth_bands": depth_bands,
        "bids": top_bids,
        "asks": top_asks,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _detect_walls(
    bids: list[tuple[Decimal, Decimal]],
    asks: list[tuple[Decimal, Decimal]],
    total_vol: Decimal,
    mid_price: Decimal,
) -> list[dict]:
    if total_vol <= 0:
        return []
    threshold = Decimal(str(settings.orderbook_wall_threshold))
    walls: list[dict] = []

    for side, levels in (("BID", bids), ("ASK", asks)):
        for price, qty in levels:
            if qty / total_vol >= threshold:
                distance_pct = float((price - mid_price) / mid_price * 100)
                walls.append({
                    "side": side,
                    "price": str(price),
                    "quantity": str(qty),
                    "pct_of_total": str(round(float(qty / total_vol * 100), 1)),
                    "distance_pct": round(distance_pct, 2),
                })
    return walls
