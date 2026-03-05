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
COOLDOWN_DEFAULT = 14400  # 4h per (symbol, level_price)
COOLDOWN_BY_TYPE = {
    "PP": 14400, "D_H": 14400, "D_L": 14400,  # 4h pivot, 4h session H/L
    "FIB_382": 21600, "FIB_50": 21600, "FIB_618": 21600,  # 6h fib
    "FIB_1272": 21600, "FIB_1618": 21600,  # 6h fib extensions
    "PSYCH": 28800,  # 8h psychological levels
}
# Types where price changes often (new daily high/low, recent swing) →
# cooldown keyed on (symbol, type) instead of (symbol, exact_price)
_TYPE_KEYED_COOLDOWNS = {"D_H", "D_L", "RH1", "RH2", "RH3", "RL1", "RL2", "RL3", "PP"}
SYMBOL_COOLDOWN = 3600  # 1h min between alerts for same symbol
ALERT_SYMBOLS = {"BTCUSDC"}  # only these symbols send Telegram level alerts

_last_prices: dict[str, Decimal] = {}
_cooldowns: dict[tuple[str, str], float] = {}
_symbol_last_alert: dict[str, float] = {}


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
    if ALERT_SYMBOLS and symbol not in ALERT_SYMBOLS:
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
    now = time.monotonic()
    # Global per-symbol rate limit
    if now - _symbol_last_alert.get(symbol, 0) < SYMBOL_COOLDOWN:
        return
    level_type = level.get("type", "")
    # For types where price shifts often, key on type not exact price
    if level_type in _TYPE_KEYED_COOLDOWNS:
        key = (symbol, level_type)
    else:
        key = (symbol, level.get("price", ""))
    cooldown = COOLDOWN_BY_TYPE.get(level_type, COOLDOWN_DEFAULT)
    if now - _cooldowns.get(key, 0) < cooldown:
        return
    _cooldowns[key] = now
    _symbol_last_alert[symbol] = now
    log.info("level_alert_triggered", symbol=symbol, level_type=level.get("type"),
             price=str(price), level_price=level.get("price"))
    asyncio.create_task(telegram_notifier.notify_level_reached(
        symbol, price, level["price"], level.get("type", ""), level.get("label", ""),
    ))
