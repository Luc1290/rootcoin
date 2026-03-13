import asyncio
import time as _time
from datetime import datetime, timezone
from decimal import Decimal

import aiohttp
import structlog

from backend.exchange import binance_client
from backend.market import macro_tracker
from backend.services import telegram_notifier
from backend.core.config import settings

log = structlog.get_logger()

STALE_THRESHOLD = 900
EXCLUDED_SUFFIXES = ("UP", "DOWN", "BEAR", "BULL")
EXCLUDED_BASES = {"FRONT"}
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker"
VALID_WINDOWS = ("15m", "1h", "4h", "24h")
WINDOW_TO_BINANCE = {"24h": "1d"}  # Binance accepts 1d not 24h
TOP_GAINER_THRESHOLD = Decimal("6")  # 24h change % to qualify as top gainer
TOP_GAINERS_MAX = 10
TOP_MOVER_AMPLITUDE_THRESHOLD = Decimal("12")  # (high-low)/low % to qualify as top mover
TOP_MOVER_MIN_VOLUME = Decimal("50000")  # min 50k USDC 24h volume to qualify
TOP_MOVERS_MAX = 10
MACRO_LABELS = {
    "sp500": "S&P 500", "nasdaq": "Nasdaq", "dxy": "Dollar", "vix": "Peur",
    "gold": "Or", "oil": "P\u00e9trole", "us10y": "Taux 10a", "us05y": "Taux 5a",
    "usdjpy": "Yen", "mstr": "MicroStrategy", "ibit": "ETF BTC", "googl": "Google",
    "nvda": "Nvidia", "cac40": "CAC 40", "dax": "DAX", "eurusd": "EUR/USD",
}
MACRO_INVERTED = {"vix", "dxy"}  # green = down for these (good for crypto)
MACRO_CRYPTO_IMPACT = {
    "dxy": "inverse", "vix": "inverse", "nasdaq": "direct", "sp500": "direct",
    "gold": "inverse", "us10y": "inverse", "us05y": "inverse", "oil": "inverse",
    "usdjpy": "direct", "mstr": "direct", "ibit": "direct", "googl": "direct",
    "nvda": "direct", "cac40": "direct", "dax": "direct", "eurusd": "inverse",
}
EARLY_MOVER_MIN_CHANGE = Decimal("0.5")  # min |change| in 5m
EARLY_MOVER_MIN_VOLUME = Decimal("10000")  # min 24h volume to avoid dust
EARLY_MOVER_SURGE_THRESHOLD = Decimal("2")  # 2x expected 5m volatility
EARLY_MOVERS_MAX = 8
SURGE_SQRT_288 = Decimal("17")  # sqrt(288 five-min periods in 24h)
NOTIFY_COOLDOWN = 4 * 3600  # 4h cooldown per symbol before re-notifying

_heatmap_cache: dict[str, dict] = {}
_active_window: str = "4h"
_refresh_task: asyncio.Task | None = None
_prev_special: set[str] = set()  # symbols in special categories last refresh
_notif_cooldown: dict[str, float] = {}  # symbol -> last notified timestamp


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
    all_assets = cache.get("assets", [])
    gainers = [a for a in all_assets if a.get("top_gainer")]
    movers = [a for a in all_assets if a.get("top_mover")]
    early = [a for a in all_assets if a.get("early_mover")]
    volume_based = [a for a in all_assets
                    if not a.get("top_gainer") and not a.get("top_mover") and not a.get("early_mover")]
    assets = early + gainers + movers + volume_based[:top_n]
    fetched = cache.get("fetched_at")
    is_stale = True
    if fetched:
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        is_stale = age > STALE_THRESHOLD
    # Build macro tiles from macro_tracker
    macro_data = macro_tracker.get_macro_data()
    macro_tiles = []
    for key, label in MACRO_LABELS.items():
        ind = macro_data.get("indicators", {}).get(key)
        if not ind:
            continue
        change = float(ind.get("change_pct", 0))
        # Compute crypto impact: green triangle if good for crypto, red if bad
        impact = MACRO_CRYPTO_IMPACT.get(key, "direct")
        change_f = float(ind.get("change_pct", 0))
        if impact == "inverse":
            crypto_good = change_f < 0
        else:
            crypto_good = change_f > 0
        crypto_icon = "up" if crypto_good else "down" if change_f != 0 else "neutral"

        macro_tiles.append({
            "key": key,
            "label": label,
            "value": ind["value"],
            "change_pct": ind["change_pct"],
            "trend": ind["trend"],
            "inverted": key in MACRO_INVERTED,
            "crypto_impact": crypto_icon,
        })

    return {
        "assets": assets,
        "macro": macro_tiles,
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
        if base in EXCLUDED_BASES:
            continue
        try:
            volume = Decimal(t["quoteVolume"])
            change_pct_24h = Decimal(t["priceChangePercent"])
            high = Decimal(t["highPrice"])
            low = Decimal(t["lowPrice"])
            amplitude = ((high - low) / low * 100) if low > 0 else Decimal("0")
        except Exception:
            continue
        candidates.append({
            "symbol": symbol, "base_asset": base,
            "volume": volume, "change_pct_24h": change_pct_24h,
            "amplitude": amplitude,
        })

    # Sort by volume, keep top N
    candidates.sort(key=lambda a: a["volume"], reverse=True)
    top_by_volume = candidates[:settings.heatmap_top_n]
    if not top_by_volume:
        return

    # Find top gainers not already in volume list
    top_symbols = {a["symbol"] for a in top_by_volume}
    gainers = [
        c for c in candidates
        if c["symbol"] not in top_symbols and c["change_pct_24h"] >= TOP_GAINER_THRESHOLD
    ]
    gainers.sort(key=lambda a: a["change_pct_24h"], reverse=True)
    for g in gainers[:TOP_GAINERS_MAX]:
        g["is_top_gainer"] = True

    # Fetch 12h rolling window for potential movers to get recent amplitude
    listed_symbols = top_symbols | {a["symbol"] for a in gainers[:TOP_GAINERS_MAX]}
    potential_mover_syms = [
        c["symbol"] for c in candidates
        if c["symbol"] not in listed_symbols
        and c["amplitude"] >= TOP_MOVER_AMPLITUDE_THRESHOLD
        and c["volume"] >= TOP_MOVER_MIN_VOLUME
    ]
    if potential_mover_syms:
        syms_param = "[" + ",".join(f'"{s}"' for s in potential_mover_syms) + "]"
        url_12h = f"{BINANCE_TICKER_URL}?windowSize=12h&symbols={syms_param}"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s12:
                async with s12.get(url_12h) as r12:
                    if r12.status == 200:
                        tickers_12h = await r12.json()
                        cand_by_sym = {c["symbol"]: c for c in candidates}
                        for t12 in tickers_12h:
                            try:
                                high = Decimal(t12["highPrice"])
                                low = Decimal(t12["lowPrice"])
                                amp = ((high - low) / low * 100) if low > 0 else Decimal("0")
                                if t12["symbol"] in cand_by_sym:
                                    cand_by_sym[t12["symbol"]]["amplitude"] = amp
                            except Exception:
                                pass
        except Exception:
            log.warning("heatmap_12h_amplitude_fetch_failed", exc_info=True)

    # Find top movers by 12h amplitude (high volatility) not already listed
    movers = [
        c for c in candidates
        if c["symbol"] not in listed_symbols
        and c["amplitude"] >= TOP_MOVER_AMPLITUDE_THRESHOLD
        and c["volume"] >= TOP_MOVER_MIN_VOLUME
    ]
    movers.sort(key=lambda a: a["amplitude"], reverse=True)
    for m in movers[:TOP_MOVERS_MAX]:
        m["is_top_mover"] = True

    # Detect early movers via 5m rolling window (scan ALL USDC pairs)
    all_listed = listed_symbols | {m["symbol"] for m in movers[:TOP_MOVERS_MAX]}
    early_movers = []
    early_candidates = [c for c in candidates if c["volume"] >= EARLY_MOVER_MIN_VOLUME]
    if early_candidates:
        try:
            url_5m = f"{BINANCE_TICKER_URL}?windowSize=5m"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s5:
                async with s5.get(url_5m) as r5:
                    if r5.status == 200:
                        tickers_5m = await r5.json()
                        change_5m_map = {}
                        for t5 in tickers_5m:
                            sym = t5.get("symbol", "")
                            if sym.endswith("USDC"):
                                try:
                                    change_5m_map[sym] = Decimal(t5["priceChangePercent"])
                                except Exception:
                                    pass

                        for c in early_candidates:
                            change_5m = change_5m_map.get(c["symbol"])
                            if change_5m is None:
                                continue
                            c["change_5m"] = change_5m
                            if abs(change_5m) < EARLY_MOVER_MIN_CHANGE:
                                continue
                            expected = c["amplitude"] / SURGE_SQRT_288
                            surge = abs(change_5m) / max(expected, Decimal("0.01"))
                            if surge >= EARLY_MOVER_SURGE_THRESHOLD:
                                c["surge_ratio"] = surge
                                if c["symbol"] not in all_listed:
                                    early_movers.append(c)
        except Exception:
            log.warning("heatmap_5m_fetch_failed", exc_info=True)

    early_movers.sort(key=lambda a: a.get("surge_ratio", 0), reverse=True)
    for em in early_movers[:EARLY_MOVERS_MAX]:
        em["is_early_mover"] = True

    top = early_movers[:EARLY_MOVERS_MAX] + movers[:TOP_MOVERS_MAX] + gainers[:TOP_GAINERS_MAX] + top_by_volume

    # Step 2: fetch rolling window for these symbols
    symbols_list = [a["symbol"] for a in top]
    # Binance requires symbols as raw JSON array in the URL (not percent-encoded by aiohttp)
    symbols_param = "[" + ",".join(f'"{s}"' for s in symbols_list) + "]"
    binance_window = WINDOW_TO_BINANCE.get(window, window)
    url = f"{BINANCE_TICKER_URL}?windowSize={binance_window}&symbols={symbols_param}"

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
        if not data["price"] or Decimal(data["price"]) <= 0:
            continue
        asset_entry = {
            "symbol": a["symbol"],
            "base_asset": a["base_asset"],
            "price": data["price"],
            "change_24h": data["change"],
            "volume_24h": str(round(a["volume"], 0)),
            "top_gainer": a.get("is_top_gainer", False),
            "top_mover": a.get("is_top_mover", False),
            "early_mover": a.get("is_early_mover", False),
            "amplitude": str(round(a.get("amplitude", Decimal("0")), 2)),
            "change_24h_pct": str(round(a["change_pct_24h"], 2)),
        }
        if "change_5m" in a:
            asset_entry["change_5m"] = str(round(a["change_5m"], 2))
        if "surge_ratio" in a:
            asset_entry["surge_ratio"] = str(round(a["surge_ratio"], 1))
        assets.append(asset_entry)

    _heatmap_cache[window] = {
        "assets": assets,
        "fetched_at": datetime.now(timezone.utc),
    }
    log.debug("heatmap_refreshed", window=window, count=len(assets))

    # Notify new entries in special categories
    await _notify_new_specials(assets)


async def _notify_new_specials(assets: list[dict]):
    global _prev_special
    now = _time.time()

    # Purge expired cooldowns
    _notif_cooldown.update({
        s: t for s, t in _notif_cooldown.items() if now - t < NOTIFY_COOLDOWN
    })

    current_early = set()
    new_entries = []

    for a in assets:
        if not a.get("early_mover"):
            continue
        sym = a["symbol"]
        base = a.get("base_asset", sym.replace("USDC", ""))
        current_early.add(sym)
        if sym not in _prev_special and sym not in _notif_cooldown:
            change = a.get("change_5m", "?")
            surge = a.get("surge_ratio", "?")
            price = a.get("price", "?")
            new_entries.append(
                f"\U0001F680 <b>{base}</b> {change}% en 5min (surge x{surge}) \u2014 ${price}"
            )
            _notif_cooldown[sym] = now

    _prev_special = current_early

    if new_entries and telegram_notifier.is_heatmap_enabled():
        msg = "\U0001F4CA <b>D\u00e9marrage d\u00e9tect\u00e9</b>\n\n" + "\n".join(new_entries)
        asyncio.create_task(telegram_notifier.notify(msg))
