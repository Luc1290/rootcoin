import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select

from backend.core.database import async_session
from backend.core.models import PriceAlert
from backend.exchange import ws_manager
from backend.exchange.ws_manager import EVENT_PRICE_UPDATE
from backend.market import market_analyzer
from backend.services import telegram_notifier

log = structlog.get_logger()

CROSS_TOLERANCE = Decimal("0.0005")  # ±0.05% band around level
COOLDOWN_DEFAULT = 14400  # 4h per (symbol, level_price)
COOLDOWN_BY_TYPE = {
    "PP": 14400, "D_H": 14400, "D_L": 14400,  # 4h pivot, 4h session H/L
    "R3": 14400, "S3": 14400,  # 4h extreme pivots
    "PDC": 14400, "VWAP": 7200,  # 4h prev close, 2h VWAP (moves intraday)
    "W_PP": 28800, "W_H": 28800, "W_L": 28800,  # 8h weekly levels
    "FIB_382": 21600, "FIB_50": 21600, "FIB_618": 21600,  # 6h fib
    "FIB_1272": 21600, "FIB_1618": 21600,  # 6h fib extensions
    "PSYCH": 28800,  # 8h psychological levels
}
# Types where price changes often (new daily high/low, recent swing) →
# cooldown keyed on (symbol, type) instead of (symbol, exact_price)
_TYPE_KEYED_COOLDOWNS = {"D_H", "D_L", "VWAP", "RH1", "RH2", "RH3", "RL1", "RL2", "RL3", "PP"}
SYMBOL_COOLDOWN = 3600  # 1h min between alerts for same symbol
ALERT_SYMBOLS = {"BTCUSDC"}  # only these symbols send Telegram level alerts

# Velocity-based cooldown reduction — bypass cooldowns during fast moves
VELOCITY_WINDOW = 60  # seconds lookback for price velocity
VELOCITY_FAST = Decimal("1")    # >1% in 60s = fast move → cooldowns /6
VELOCITY_EXTREME = Decimal("2.5")  # >2.5% in 60s = extreme → cooldowns /20

_last_prices: dict[str, Decimal] = {}
_cooldowns: dict[tuple[str, str], float] = {}
_symbol_last_alert: dict[str, float] = {}
_price_history: dict[str, deque[tuple[float, Decimal]]] = {}  # symbol → deque of (mono_time, price)

# Custom price alerts loaded from DB, refreshed periodically
_custom_alerts: list[PriceAlert] = []
_custom_last_refresh: float = 0
_CUSTOM_REFRESH_INTERVAL = 30  # reload from DB every 30s


async def start():
    await _refresh_custom_alerts()
    ws_manager.on(EVENT_PRICE_UPDATE, _on_price)
    log.info("level_alert_started")


async def stop():
    _last_prices.clear()
    _cooldowns.clear()
    _custom_alerts.clear()
    _price_history.clear()
    log.info("level_alert_stopped")


async def _refresh_custom_alerts():
    global _custom_alerts, _custom_last_refresh
    try:
        async with async_session() as session:
            result = await session.execute(
                select(PriceAlert).where(PriceAlert.is_active == True)
            )
            _custom_alerts = list(result.scalars().all())
            # Detach from session
            for a in _custom_alerts:
                session.expunge(a)
        _custom_last_refresh = time.monotonic()
    except Exception:
        log.error("custom_alerts_refresh_failed", exc_info=True)


def _get_velocity(symbol: str) -> Decimal:
    """Return abs % price change over the velocity window. 0 if not enough data."""
    buf = _price_history.get(symbol)
    if not buf or len(buf) < 2:
        return Decimal(0)
    now = time.monotonic()
    cutoff = now - VELOCITY_WINDOW
    # Find oldest price still within window
    while len(buf) > 1 and buf[0][0] < cutoff:
        buf.popleft()
    if not buf:
        return Decimal(0)
    oldest_price = buf[0][1]
    newest_price = buf[-1][1]
    if oldest_price <= 0:
        return Decimal(0)
    return abs(newest_price - oldest_price) / oldest_price * 100


def _cooldown_divisor(symbol: str) -> int:
    """Return cooldown divisor based on current price velocity."""
    vel = _get_velocity(symbol)
    if vel >= VELOCITY_EXTREME:
        return 20
    if vel >= VELOCITY_FAST:
        return 6
    return 1


async def _on_price(msg: dict):
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

    # Track price history for velocity calculation (only for alert symbols)
    if not ALERT_SYMBOLS or symbol in ALERT_SYMBOLS:
        now = time.monotonic()
        if symbol not in _price_history:
            _price_history[symbol] = deque(maxlen=300)
        _price_history[symbol].append((now, price))

    # Check custom price alerts for ALL symbols (no ALERT_SYMBOLS filter)
    if prev is not None:
        await _check_custom_alerts(symbol, prev, price)

    # Existing level alerts only for allowed symbols
    if not telegram_notifier.is_levels_enabled():
        return
    if ALERT_SYMBOLS and symbol not in ALERT_SYMBOLS:
        return
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
            approach = "below" if prev < band_low else "above"
            _try_alert(symbol, price, prev, level, approach, analysis)


async def _check_custom_alerts(symbol: str, prev: Decimal, price: Decimal):
    now = time.monotonic()
    if now - _custom_last_refresh > _CUSTOM_REFRESH_INTERVAL:
        await _refresh_custom_alerts()

    triggered_ids = []
    for alert in _custom_alerts:
        if alert.symbol != symbol:
            continue
        tp = alert.target_price
        if alert.direction == "above" and prev < tp and price >= tp:
            triggered_ids.append(alert)
        elif alert.direction == "below" and prev > tp and price <= tp:
            triggered_ids.append(alert)

    for alert in triggered_ids:
        log.info("custom_price_alert_triggered", symbol=symbol,
                 target=str(alert.target_price), direction=alert.direction)
        asyncio.create_task(telegram_notifier.notify_price_alert(
            symbol, price, alert.target_price, alert.direction, alert.note,
        ))
        asyncio.create_task(_deactivate_alert(alert.id))
        asyncio.create_task(_broadcast_alert_triggered(alert, price))
        _custom_alerts.remove(alert)


async def _broadcast_alert_triggered(alert, price: Decimal):
    try:
        from backend.routes.ws_dashboard import _broadcast
        await _broadcast({
            "type": "alert_triggered",
            "data": {
                "id": alert.id,
                "symbol": alert.symbol,
                "target_price": str(alert.target_price),
                "direction": alert.direction,
                "price": str(price),
            },
        })
    except Exception:
        pass


async def _deactivate_alert(alert_id: int):
    try:
        async with async_session() as session:
            alert = await session.get(PriceAlert, alert_id)
            if alert:
                alert.is_active = False
                alert.triggered_at = datetime.now(timezone.utc)
                await session.commit()
    except Exception:
        log.error("deactivate_alert_failed", alert_id=alert_id, exc_info=True)


def _interpret_level(level_type: str, approach: str) -> tuple[str, str]:
    """Return (interpretation, opportunity) based on level type and approach direction."""
    # Resistance-like levels
    resistance_types = {"R1", "R2", "R3", "D_H", "W_H", "SW_H", "FIB_1272", "FIB_1618"}
    # Support-like levels
    support_types = {"S1", "S2", "S3", "D_L", "W_L", "SW_L"}
    # Fib retracement (support in uptrend, resistance in downtrend)
    fib_types = {"FIB_382", "FIB_50", "FIB_618"}
    # Neutral levels
    neutral_types = {"PP", "W_PP", "PDC", "VWAP", "PSYCH"}

    if level_type in resistance_types:
        if approach == "below":
            return "Test de résistance", "\U0001f534 Rejet probable \u2192 short"
        else:
            return "Cassure résistance par le haut", "\U0001f7e2 Continuation haussière \u2192 long"
    elif level_type in support_types:
        if approach == "above":
            return "Test de support", "\U0001f7e2 Rebond probable \u2192 long"
        else:
            return "Cassure support par le bas", "\U0001f534 Continuation baissière \u2192 short"
    elif level_type in fib_types:
        if approach == "above":
            return "Test retracement Fib", "\U0001f7e2 Rebond probable \u2192 long"
        else:
            return "Cassure Fib par le bas", "\U0001f534 Continuation baissière \u2192 short"
    elif level_type in neutral_types:
        if approach == "below":
            return "Franchissement par le bas", "\U0001f7e2 Signal haussier \u2192 long"
        else:
            return "Franchissement par le haut", "\U0001f534 Signal baissier \u2192 short"
    else:
        if approach == "below":
            return "Approche par le bas", "\u2753 À surveiller"
        else:
            return "Approche par le haut", "\u2753 À surveiller"


def _try_alert(symbol: str, price: Decimal, prev: Decimal,
               level: dict, approach: str, analysis: dict):
    now = time.monotonic()
    divisor = _cooldown_divisor(symbol)
    # Global per-symbol rate limit
    if now - _symbol_last_alert.get(symbol, 0) < SYMBOL_COOLDOWN / divisor:
        return
    level_type = level.get("type", "")
    # For types where price shifts often, key on type not exact price
    if level_type in _TYPE_KEYED_COOLDOWNS:
        key = (symbol, level_type)
    else:
        key = (symbol, level.get("price", ""))
    cooldown = COOLDOWN_BY_TYPE.get(level_type, COOLDOWN_DEFAULT)
    if now - _cooldowns.get(key, 0) < cooldown / divisor:
        return
    _cooldowns[key] = now
    _symbol_last_alert[symbol] = now
    velocity_tag = "" if divisor == 1 else " \u26a1" if divisor == 6 else " \u26a1\u26a1"

    interpretation, opportunity = _interpret_level(level_type, approach)

    bias = analysis.get("bias", {})
    bias_dir = bias.get("direction", "")
    bias_score = bias.get("confidence", 0)

    log.info("level_alert_triggered", symbol=symbol, level_type=level.get("type"),
             price=str(price), level_price=level.get("price"),
             approach=approach, velocity_divisor=divisor)
    asyncio.create_task(telegram_notifier.notify_level_reached(
        symbol, price, level["price"], level.get("type", ""),
        level.get("label", "") + velocity_tag,
        approach, interpretation, opportunity, bias_dir, bias_score,
    ))
