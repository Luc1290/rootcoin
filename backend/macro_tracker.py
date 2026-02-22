import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import structlog

from backend.config import settings

log = structlog.get_logger()

STALE_THRESHOLD = 900  # 15 min
TREND_THRESHOLD = Decimal("0.3")  # % change to qualify as up/down

TICKERS = {
    "dxy": "DX-Y.NYB",
    "vix": "^VIX",
    "nasdaq": "^NDX",
    "gold": "GC=F",
    "us10y": "^TNX",
    "us05y": "^FVX",
    "oil": "CL=F",
    "usdjpy": "USDJPY=X",
}

# Yahoo yield indices report yield * 10 (e.g. 42.5 = 4.25%)
YIELD_TICKERS = {"us10y", "us05y"}

_macro_cache: dict = {}
_refresh_task: asyncio.Task | None = None


async def start():
    global _refresh_task
    _refresh_task = asyncio.create_task(_run_refresh())
    log.info("macro_tracker_started")


async def stop():
    if _refresh_task:
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
    log.info("macro_tracker_stopped")


def get_macro_data() -> dict:
    if not _macro_cache:
        return {"is_stale": True, "indicators": {}}
    fetched = _macro_cache.get("fetched_at")
    is_stale = True
    if fetched:
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        is_stale = age > STALE_THRESHOLD
    return {
        "indicators": _macro_cache.get("indicators", {}),
        "fetched_at": fetched.isoformat() if fetched else None,
        "is_stale": is_stale,
    }


async def _run_refresh():
    # Fetch immediately on start, then every interval
    while True:
        try:
            await _fetch_macro()
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("macro_fetch_failed", exc_info=True)
        await asyncio.sleep(settings.macro_refresh_interval)


async def _fetch_macro():
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _sync_fetch)
    if data:
        _macro_cache["indicators"] = data
        _macro_cache["fetched_at"] = datetime.now(timezone.utc)
        log.info("macro_data_refreshed", indicators=list(data.keys()))


def _sync_fetch() -> dict:
    import yfinance as yf

    result = {}
    for key, symbol in TICKERS.items():
        try:
            tk = yf.Ticker(symbol)
            hist = tk.history(period="5d", interval="1d")
            if hist.empty:
                log.warning("macro_no_data", ticker=symbol)
                # Keep previous value if exists
                prev = _macro_cache.get("indicators", {}).get(key)
                if prev:
                    result[key] = prev
                continue

            current = Decimal(str(round(hist["Close"].iloc[-1], 2)))
            prev_close = Decimal(str(round(hist["Close"].iloc[-2], 2))) if len(hist) >= 2 else current

            # Yield tickers: convert from yield*10 to actual %
            if key in YIELD_TICKERS:
                current = current / 10
                prev_close = prev_close / 10

            try:
                change_pct = ((current - prev_close) / prev_close * 100) if prev_close else Decimal("0")
            except (InvalidOperation, ZeroDivisionError):
                change_pct = Decimal("0")

            if change_pct > TREND_THRESHOLD:
                trend = "up"
            elif change_pct < -TREND_THRESHOLD:
                trend = "down"
            else:
                trend = "flat"

            result[key] = {
                "value": str(current),
                "prev_close": str(prev_close),
                "change_pct": str(round(change_pct, 2)),
                "trend": trend,
            }
        except Exception:
            log.warning("macro_ticker_failed", ticker=symbol, exc_info=True)
            prev = _macro_cache.get("indicators", {}).get(key)
            if prev:
                result[key] = prev

    # Compute yield curve spread (10Y - 5Y)
    us10y = result.get("us10y")
    us05y = result.get("us05y")
    if us10y and us05y:
        try:
            spread_val = Decimal(us10y["value"]) - Decimal(us05y["value"])
            prev_spread = Decimal(us10y["prev_close"]) - Decimal(us05y["prev_close"])
            try:
                spread_change = ((spread_val - prev_spread) / abs(prev_spread) * 100) if prev_spread else Decimal("0")
            except (InvalidOperation, ZeroDivisionError):
                spread_change = Decimal("0")

            if spread_val < 0:
                trend = "inverted"
            elif spread_change > TREND_THRESHOLD:
                trend = "up"
            elif spread_change < -TREND_THRESHOLD:
                trend = "down"
            else:
                trend = "flat"

            result["spread"] = {
                "value": str(round(spread_val, 3)),
                "prev_close": str(round(prev_spread, 3)),
                "change_pct": str(round(spread_change, 2)),
                "trend": trend,
            }
        except Exception:
            log.warning("spread_computation_failed", exc_info=True)

    return result
