import asyncio
from decimal import Decimal, ROUND_DOWN

import structlog

from backend.exchange import binance_client

log = structlog.get_logger()

REFRESH_INTERVAL = 3600

_filters: dict[str, dict] = {}
_refresh_task: asyncio.Task | None = None


async def init_filters():
    await _load()
    global _refresh_task
    _refresh_task = asyncio.create_task(_run_refresh())
    log.info("symbol_filters_initialized", count=len(_filters))


async def stop():
    if _refresh_task:
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass


async def _load():
    info = await binance_client.get_exchange_info()
    filters = {}
    for sym in info.get("symbols", []):
        symbol = sym["symbol"]
        f: dict = {}
        for flt in sym.get("filters", []):
            ft = flt["filterType"]
            if ft == "LOT_SIZE":
                f["step_size"] = Decimal(flt["stepSize"])
                f["min_qty"] = Decimal(flt["minQty"])
                f["max_qty"] = Decimal(flt["maxQty"])
            elif ft == "PRICE_FILTER":
                f["tick_size"] = Decimal(flt["tickSize"])
                f["min_price"] = Decimal(flt["minPrice"])
                f["max_price"] = Decimal(flt["maxPrice"])
            elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
                f["min_notional"] = Decimal(flt.get("minNotional", "0"))
        if f:
            filters[symbol] = f
    _filters.update(filters)


async def _run_refresh():
    while True:
        try:
            await asyncio.sleep(REFRESH_INTERVAL)
            await _load()
            log.info("symbol_filters_refreshed", count=len(_filters))
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("symbol_filters_refresh_failed", exc_info=True)


def _round_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def round_quantity(symbol: str, qty: Decimal) -> Decimal:
    f = _filters.get(symbol, {})
    step = f.get("step_size")
    if step:
        return _round_step(qty, step)
    return qty


def round_price(symbol: str, price: Decimal) -> Decimal:
    f = _filters.get(symbol, {})
    tick = f.get("tick_size")
    if tick:
        return _round_step(price, tick)
    return price


def validate_order(symbol: str, qty: Decimal, price: Decimal):
    f = _filters.get(symbol)
    if not f:
        return

    min_qty = f.get("min_qty", Decimal("0"))
    max_qty = f.get("max_qty", Decimal("0"))
    if min_qty and qty < min_qty:
        raise ValueError(f"{symbol}: qty {qty} below min {min_qty}")
    if max_qty and qty > max_qty:
        raise ValueError(f"{symbol}: qty {qty} above max {max_qty}")

    min_price = f.get("min_price", Decimal("0"))
    max_price = f.get("max_price", Decimal("0"))
    if min_price and price < min_price:
        raise ValueError(f"{symbol}: price {price} below min {min_price}")
    if max_price and price > max_price:
        raise ValueError(f"{symbol}: price {price} above max {max_price}")

    min_notional = f.get("min_notional", Decimal("0"))
    if min_notional and qty * price < min_notional:
        raise ValueError(f"{symbol}: notional {qty * price} below min {min_notional}")
