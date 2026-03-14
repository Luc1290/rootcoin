import asyncio
import time as _time
from dataclasses import dataclass
from decimal import Decimal

import aiohttp
import structlog

from backend.core.config import settings
from backend.market import heatmap_manager
from backend.services import telegram_notifier

log = structlog.get_logger()

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker"

# Symbols to monitor (top coins by market cap, not shitcoins)
DEFAULT_SYMBOLS = "BTCUSDC,ETHUSDC"

# Detection thresholds per window
WINDOWS = {
    "15m": {
        "sqrt_periods": Decimal("9.8"),   # sqrt(96 periods/day)
        "min_change": Decimal("0.3"),      # absolute minimum %
        "surge_threshold": Decimal("1.5"),
        "realert_step": Decimal("0.4"),    # re-alert when move extends by this
    },
    "1h": {
        "sqrt_periods": Decimal("4.9"),   # sqrt(24 periods/day)
        "min_change": Decimal("0.6"),
        "surge_threshold": Decimal("1.5"),
        "realert_step": Decimal("0.7"),
    },
}

VOLUME_HIGH_RATIO = Decimal("1.5")  # 1.5x expected volume = "gros volume"
STATE_DECAY = 1800  # 30min — reset state if no further move
AMPLITUDE_CACHE_TTL = 900  # 15min — refresh 24h amplitude cache

_poll_task: asyncio.Task | None = None
_amplitude_cache: dict[str, Decimal] = {}  # symbol -> 24h amplitude %
_volume_24h_cache: dict[str, Decimal] = {}  # symbol -> 24h quote volume
_amplitude_fetched_at: float = 0


@dataclass
class MomentumState:
    direction: str        # "up" or "down"
    last_change: Decimal  # abs change % when last alerted
    last_alert_at: float  # monotonic time


_state: dict[tuple[str, str], MomentumState] = {}  # (symbol, window) -> state


def _get_symbols() -> list[str]:
    raw = getattr(settings, "momentum_symbols", "") or DEFAULT_SYMBOLS
    base = [s.strip() for s in raw.split(",") if s.strip()]
    # Merge heatmap gainers for dynamic monitoring
    gainer_syms = heatmap_manager.get_gainer_symbols()
    seen = set(base)
    for s in gainer_syms:
        if s not in seen:
            base.append(s)
            seen.add(s)
    return base


async def start():
    global _poll_task
    _poll_task = asyncio.create_task(_run_poll())
    log.info("momentum_alert_started", symbols=len(_get_symbols()))


async def stop():
    if _poll_task:
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
    log.info("momentum_alert_stopped")


async def _run_poll():
    await asyncio.sleep(10)  # let other modules init
    while True:
        try:
            await _check_all()
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("momentum_poll_failed", exc_info=True)
        await asyncio.sleep(settings.momentum_poll_interval)


async def _check_all():
    symbols = _get_symbols()
    if not symbols:
        return

    await _refresh_amplitudes(symbols)

    # Fetch 15m and 1h rolling tickers in parallel
    results = await asyncio.gather(
        _fetch_tickers(symbols, "15m"),
        _fetch_tickers(symbols, "1h"),
        return_exceptions=True,
    )

    now = _time.monotonic()
    _purge_stale_states(now)

    for i, window in enumerate(("15m", "1h")):
        tickers = results[i]
        if isinstance(tickers, Exception):
            log.warning("momentum_fetch_error", window=window, error=str(tickers))
            continue
        if not tickers:
            continue
        await _check_window(window, tickers, now)


async def _fetch_tickers(symbols: list[str], window: str) -> list[dict]:
    syms_param = "[" + ",".join(f'"{s}"' for s in symbols) + "]"
    url = f"{BINANCE_TICKER_URL}?windowSize={window}&symbols={syms_param}"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return []
            return await resp.json()


async def _refresh_amplitudes(symbols: list[str]):
    global _amplitude_fetched_at
    now = _time.monotonic()
    if now - _amplitude_fetched_at < AMPLITUDE_CACHE_TTL:
        return
    try:
        syms_param = "[" + ",".join(f'"{s}"' for s in symbols) + "]"
        url = f"{BINANCE_TICKER_URL}?windowSize=1d&symbols={syms_param}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return
                tickers = await resp.json()
        for t in tickers:
            try:
                high = Decimal(t["highPrice"])
                low = Decimal(t["lowPrice"])
                amp = ((high - low) / low * 100) if low > 0 else Decimal("0")
                _amplitude_cache[t["symbol"]] = amp
                _volume_24h_cache[t["symbol"]] = Decimal(t["quoteVolume"])
            except Exception:
                pass
        _amplitude_fetched_at = now
    except Exception:
        log.warning("momentum_amplitude_fetch_failed", exc_info=True)


async def _check_window(window: str, tickers: list[dict], now: float):
    cfg = WINDOWS[window]

    for t in tickers:
        sym = t.get("symbol", "")
        try:
            change = Decimal(t["priceChangePercent"])
            quote_vol = Decimal(t["quoteVolume"])
            price = t["lastPrice"]
        except Exception:
            continue

        abs_change = abs(change)
        if abs_change < cfg["min_change"]:
            continue

        amplitude = min(_amplitude_cache.get(sym, Decimal("0")), Decimal("15"))
        expected = amplitude / cfg["sqrt_periods"]
        surge = abs_change / max(expected, Decimal("0.01"))

        if surge < cfg["surge_threshold"]:
            continue

        direction = "up" if change > 0 else "down"
        key = (sym, window)

        if not _should_alert(key, direction, abs_change, cfg["realert_step"], now):
            continue

        # Volume ratio
        vol_24h = _volume_24h_cache.get(sym, Decimal("0"))
        periods = int(cfg["sqrt_periods"] ** 2)  # 96 for 15m, 24 for 1h
        expected_vol = vol_24h / max(periods, 1)
        vol_ratio = quote_vol / max(expected_vol, Decimal("1"))

        base = sym.replace("USDC", "").replace("USDT", "")
        is_continued = key in _state
        await _notify(base, direction, change, window, price, vol_ratio, is_continued)

        _state[key] = MomentumState(
            direction=direction,
            last_change=abs_change,
            last_alert_at=now,
        )


def _should_alert(
    key: tuple[str, str], direction: str, abs_change: Decimal,
    realert_step: Decimal, now: float,
) -> bool:
    state = _state.get(key)
    if not state:
        return True

    # Direction reversal -> always alert
    if state.direction != direction:
        return True

    # Same direction -> only if move extended enough
    return abs_change >= state.last_change + realert_step


def _purge_stale_states(now: float):
    expired = [k for k, s in _state.items() if now - s.last_alert_at > STATE_DECAY]
    for k in expired:
        del _state[k]


async def _notify(
    base: str, direction: str, change: Decimal, window: str,
    price: str, vol_ratio: Decimal, is_continued: bool,
):
    if not telegram_notifier.is_momentum_enabled():
        return

    if direction == "up":
        emoji = "\U0001f4c8\U0001f4c8" if is_continued else "\U0001f4c8"
        verb = "continue" if is_continued else "monte"
    else:
        emoji = "\U0001f4c9\U0001f4c9" if is_continued else "\U0001f4c9"
        verb = "continue" if is_continued else "baisse"

    sign = "+" if change > 0 else ""
    vol_tag = " — gros volume" if vol_ratio >= VOLUME_HIGH_RATIO else ""

    msg = (
        f"{emoji} <b>{base} {verb} {sign}{change}% en {window}</b>"
        f"{vol_tag}\n"
        f"${price}"
    )
    asyncio.create_task(telegram_notifier.notify(msg))
