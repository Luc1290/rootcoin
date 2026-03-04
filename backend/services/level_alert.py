import asyncio
import time
from decimal import Decimal

import structlog

from backend.exchange import ws_manager
from backend.exchange.ws_manager import EVENT_PRICE_UPDATE
from backend.market import market_analyzer
from backend.services import telegram_notifier

log = structlog.get_logger()

CROSS_TOLERANCE = Decimal("0.0005")  # ±0.05% band around level
COOLDOWN_DEFAULT = 3600  # 1h per (symbol, level_price)
COOLDOWN_BY_TYPE = {"PP": 14400, "D_H": 7200, "D_L": 7200}  # 4h pivot, 2h session H/L

_last_prices: dict[str, Decimal] = {}
_cooldowns: dict[tuple[str, str], float] = {}


async def start():
    ws_manager.on(EVENT_PRICE_UPDATE, _on_price)
    log.info("level_alert_started")


async def stop():
    _last_prices.clear()
    _cooldowns.clear()
    log.info("level_alert_stopped")


async def _on_price(msg: dict):
    if not telegram_notifier.is_levels_enabled():
        return

    symbol = msg.get("s", "")
    price_str = msg.get("c", "")
    if not symbol or not price_str:
        return

    try:
        price = Decimal(price_str)
    except Exception:
        return
    if price <= 0:
        return

    prev = _last_prices.get(symbol)
    _last_prices[symbol] = price
    if prev is None:
        return

    analysis = market_analyzer.get_analysis(symbol)
    if not analysis:
        return

    levels = analysis.get("key_levels", [])
    for level in levels:
        level_price_str = level.get("price")
        if not level_price_str:
            continue
        try:
            level_price = Decimal(level_price_str)
        except Exception:
            continue
        if level_price <= 0:
            continue

        band = level_price * CROSS_TOLERANCE
        band_low = level_price - band
        band_high = level_price + band

        was_outside = prev < band_low or prev > band_high
        is_inside = band_low <= price <= band_high

        if was_outside and is_inside:
            _try_alert(symbol, price, level)


def _try_alert(symbol: str, price: Decimal, level: dict):
    key = (symbol, level.get("price", ""))
    now = time.monotonic()
    level_type = level.get("type", "")
    cooldown = COOLDOWN_BY_TYPE.get(level_type, COOLDOWN_DEFAULT)
    if now - _cooldowns.get(key, 0) < cooldown:
        return
    _cooldowns[key] = now
    log.info("level_alert_triggered", symbol=symbol, level_type=level.get("type"),
             price=str(price), level_price=level.get("price"))
    asyncio.create_task(telegram_notifier.notify_level_reached(
        symbol, price, level["price"], level.get("type", ""), level.get("label", ""),
    ))
