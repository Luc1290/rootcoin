import asyncio
import time as _time
from datetime import datetime, timezone
from decimal import Decimal

import aiohttp
import structlog

from backend.exchange import binance_client
from backend.market import macro_tracker
from backend.services import notification_logger, telegram_notifier
from backend.core.config import settings

log = structlog.get_logger()

STALE_THRESHOLD = 900
EXCLUDED_SUFFIXES = ("UP", "DOWN", "BEAR", "BULL")
EXCLUDED_BASES = {"FRONT"}
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker"
VALID_WINDOWS = ("15m", "1h", "4h", "24h")
WINDOW_TO_BINANCE = {"24h": "1d"}  # Binance accepts 1d not 24h
TOP_GAINER_THRESHOLD = Decimal("4")  # 24h change % to qualify as top gainer
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
EARLY_MOVER_MIN_CHANGE = Decimal("0.3")  # min |change| in 5m
EARLY_MOVER_MIN_VOLUME = Decimal("2000")  # min 24h volume to avoid dust
EARLY_MOVER_MIN_VOL_5M = Decimal("500")  # min 5m quote volume to confirm real activity
EARLY_MOVER_SURGE_THRESHOLD = Decimal("1.2")  # 1.2x expected 5m volatility
EARLY_MOVERS_MAX = 8
SURGE_SQRT_288 = Decimal("17")  # sqrt(288 five-min periods in 24h)
NOTIFY_COOLDOWN = 3600  # 1h cooldown per symbol before re-notifying
MARKET_WIDE_SYMBOLS = {"BTCUSDC", "ETHUSDC"}
MARKET_WIDE_THRESHOLD = 5  # if >= 5 symbols surge, it's a market move

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

    # Detect early movers via 5m rolling window (scan ALL USDC pairs, including top volume)
    early_movers = []
    early_candidates = [c for c in candidates if c["volume"] >= EARLY_MOVER_MIN_VOLUME]
    if early_candidates:
        try:
            early_syms_list = [c["symbol"] for c in early_candidates]
            batch_size = 100
            tickers_5m = []
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s5:
                for i in range(0, len(early_syms_list), batch_size):
                    batch = early_syms_list[i:i + batch_size]
                    syms_param = "[" + ",".join(f'"{s}"' for s in batch) + "]"
                    url_5m = f"{BINANCE_TICKER_URL}?windowSize=5m&symbols={syms_param}"
                    async with s5.get(url_5m) as r5:
                        if r5.status == 200:
                            tickers_5m.extend(await r5.json())
                        else:
                            log.warning("heatmap_5m_api_error", status=r5.status, batch=i)

            change_5m_map = {}
            vol_5m_map = {}
            for t5 in tickers_5m:
                sym = t5.get("symbol", "")
                try:
                    change_5m_map[sym] = Decimal(t5["priceChangePercent"])
                    vol_5m_map[sym] = Decimal(t5["quoteVolume"])
                except Exception:
                    pass

            for c in early_candidates:
                change_5m = change_5m_map.get(c["symbol"])
                if change_5m is None:
                    continue
                c["change_5m"] = change_5m
                if abs(change_5m) < EARLY_MOVER_MIN_CHANGE:
                    continue
                vol_5m = vol_5m_map.get(c["symbol"], Decimal("0"))
                if vol_5m < EARLY_MOVER_MIN_VOL_5M:
                    continue
                capped_amp = min(c["amplitude"], Decimal("15"))
                expected = capped_amp / SURGE_SQRT_288
                surge = abs(change_5m) / max(expected, Decimal("0.01"))
                if surge >= EARLY_MOVER_SURGE_THRESHOLD:
                    c["surge_ratio"] = surge
                    c["vol_5m"] = vol_5m
                    early_movers.append(c)
        except Exception:
            log.warning("heatmap_5m_fetch_failed", exc_info=True)

    early_movers.sort(key=lambda a: a.get("surge_ratio", 0), reverse=True)
    early_top = early_movers[:EARLY_MOVERS_MAX]
    early_syms = {em["symbol"] for em in early_top}
    for em in early_top:
        em["is_early_mover"] = True
    # Also flag symbols already in other lists (top volume, gainers, movers)
    for a in top_by_volume + gainers[:TOP_GAINERS_MAX] + movers[:TOP_MOVERS_MAX]:
        if a["symbol"] in early_syms:
            a["is_early_mover"] = True
            match = next(em for em in early_top if em["symbol"] == a["symbol"])
            a["surge_ratio"] = match["surge_ratio"]
            if "change_5m" not in a:
                a["change_5m"] = match["change_5m"]
            if "vol_5m" not in a:
                a["vol_5m"] = match.get("vol_5m")

    # Merge all lists, deduplicate by symbol (keep first occurrence = priority order)
    seen = set()
    top = []
    for a in early_top + movers[:TOP_MOVERS_MAX] + gainers[:TOP_GAINERS_MAX] + top_by_volume:
        if a["symbol"] not in seen:
            seen.add(a["symbol"])
            top.append(a)

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
            "change_window": data["change"],
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
        if "vol_5m" in a:
            asset_entry["vol_5m"] = str(round(a["vol_5m"], 0))
        assets.append(asset_entry)

    _heatmap_cache[window] = {
        "assets": assets,
        "fetched_at": datetime.now(timezone.utc),
    }
    log.debug("heatmap_refreshed", window=window, count=len(assets))

    # Notify new entries in special categories
    await _notify_new_specials(assets)


def get_gainer_symbols() -> list[str]:
    """Return symbols currently flagged as top gainers (for momentum monitoring)."""
    cache = _heatmap_cache.get("4h", {})
    assets = cache.get("assets", [])
    return [
        a["symbol"] for a in assets
        if a.get("top_gainer") or float(a.get("change_24h_pct", 0)) >= 4
    ]


def _fmt_vol(vol) -> str:
    try:
        v = float(vol)
    except (TypeError, ValueError):
        return ""
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:.0f}"


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
            vol_5m = a.get("vol_5m", "?")
            new_entries.append({
                "sym": sym, "base": base,
                "change": change, "surge": surge, "price": price,
                "vol_5m": vol_5m,
            })
            _notif_cooldown[sym] = now

    _prev_special = current_early

    if not new_entries:
        return

    # Detect market-wide move: BTC/ETH surging or many symbols at once
    new_syms = {e["sym"] for e in new_entries}
    majors = new_syms & MARKET_WIDE_SYMBOLS
    is_market_wide = bool(majors) or len(new_entries) >= MARKET_WIDE_THRESHOLD

    if is_market_wide:
        lines = []
        for e in new_entries[:8]:
            vol = _fmt_vol(e["vol_5m"])
            lines.append(
                f"\U0001F680 <b>{e['base']}</b> {e['change']}% en 5min "
                f"(x{e['surge']}) {vol}"
            )
        msg = (
            "\U0001F30A <b>March\u00e9 en mouvement</b>\n\n"
            + "\n".join(lines)
        )
    else:
        lines = [
            f"\U0001F680 <b>{e['base']}</b> {e['change']}% en 5min "
            f"(x{e['surge']}) {_fmt_vol(e['vol_5m'])} \u2014 ${e['price']}"
            for e in new_entries
        ]
        msg = "\U0001F4CA <b>D\u00e9marrage d\u00e9tect\u00e9</b>\n\n" + "\n".join(lines)

    sent = False
    if telegram_notifier.is_heatmap_enabled():
        sent = await telegram_notifier.notify(msg)

    for e in new_entries:
        try:
            change_val = Decimal(str(e["change"])) if e["change"] != "?" else Decimal("0")
            price_val = Decimal(str(e["price"])) if e["price"] != "?" else Decimal("0")
            vol_val = Decimal(str(e["vol_5m"])) if e.get("vol_5m") not in (None, "?") else None
            surge_val = Decimal(str(e["surge"])) if e.get("surge") not in (None, "?") else None
        except Exception:
            continue
        await notification_logger.record(
            notif_type="early_mover", symbol=e["sym"],
            direction="up" if change_val > 0 else "down",
            change_pct=abs(change_val), window="5m",
            price=price_val, message=msg, telegram_sent=sent,
            volume=vol_val, surge_ratio=surge_val,
        )
