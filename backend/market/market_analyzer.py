import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from backend.market import kline_manager, macro_tracker, orderbook_tracker, whale_tracker
from backend.market.analysis_formatter import signal_to_dict, format_qty, TIMEFRAMES
from backend.scoring import signal_engine, scorer, timing_coach
from backend.trading import position_tracker
from backend.core.config import settings

log = structlog.get_logger()

STALE_THRESHOLD = 900
_API_SEMAPHORE = asyncio.Semaphore(5)
BIAS_THRESHOLD = 30  # Hysteresis: direction sticks unless new score > 30

_analysis_cache: dict[str, dict] = {}
_prev_direction: dict[str, str] = {}
_cache_time: datetime | None = None
_refresh_task: asyncio.Task | None = None


async def start():
    global _refresh_task
    _refresh_task = asyncio.create_task(_run_refresh())
    log.info("market_analyzer_started")


async def stop():
    if _refresh_task:
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
    log.info("market_analyzer_stopped")


def get_analysis(symbol: str) -> dict | None:
    return _analysis_cache.get(symbol)


async def ensure_analysis(symbol: str, timeout: float = 30.0) -> dict | None:
    """Compute analysis on-demand if not cached. Returns cached or freshly computed."""
    cached = _analysis_cache.get(symbol)
    if cached:
        return cached
    try:
        async with asyncio.timeout(timeout):
            analysis = await _analyze_symbol(symbol)
            if analysis:
                _analysis_cache[symbol] = analysis
            return analysis
    except (TimeoutError, asyncio.TimeoutError):
        log.warning("ensure_analysis_timeout", symbol=symbol, timeout=timeout)
        return None
    except Exception:
        log.warning("ensure_analysis_failed", symbol=symbol, exc_info=True)
        return None


def get_all_analyses() -> dict:
    macro = macro_tracker.get_macro_data()
    whales = whale_tracker.get_whale_alerts()
    orderbook = orderbook_tracker.get_orderbook_data()
    is_stale = True
    if _cache_time:
        age = (datetime.now(timezone.utc) - _cache_time).total_seconds()
        is_stale = age > STALE_THRESHOLD
    return {
        "analyses": list(_analysis_cache.values()),
        "macro": macro,
        "whale_alerts": whales,
        "orderbook": orderbook,
        "computed_at": _cache_time.isoformat() if _cache_time else None,
        "is_stale": is_stale,
    }


# ── Refresh loop ──────────────────────────────────────────────

async def _run_refresh():
    await asyncio.sleep(15)
    while True:
        try:
            await _compute_all()
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("analysis_refresh_failed", exc_info=True)
        await asyncio.sleep(settings.analysis_refresh_interval)


async def _compute_all():
    global _cache_time
    symbols = _get_symbols()

    async def _safe_analyze(symbol: str) -> tuple[str, dict | None]:
        async with _API_SEMAPHORE:
            try:
                return symbol, await _analyze_symbol(symbol)
            except Exception:
                log.warning("analysis_symbol_failed", symbol=symbol, exc_info=True)
                return symbol, None

    results = await asyncio.gather(*[_safe_analyze(s) for s in symbols])
    for symbol, analysis in results:
        if analysis:
            _analysis_cache[symbol] = analysis
    _cache_time = datetime.now(timezone.utc)
    log.debug("analysis_refreshed", symbols=len(symbols))


def _get_symbols() -> list[str]:
    symbols = set(settings.watchlist)
    for pos in position_tracker.get_positions():
        symbols.add(pos.symbol)
    return sorted(symbols)


# ── Per-symbol analysis ───────────────────────────────────────

async def _analyze_symbol(symbol: str) -> dict:
    # Key levels first (needed for structure detection)
    key_levels = await _compute_key_levels(symbol)

    current_price = None
    if key_levels:
        current_price = key_levels[0].get("current_price")

    # Extract signals per timeframe via unified signal engine
    signals_5m = await signal_engine.extract_signals(symbol, "5m", key_levels)
    signals_15m = await signal_engine.extract_signals(symbol, "15m", key_levels)
    signals_1h = await signal_engine.extract_signals(symbol, "1h", key_levels)
    signals_4h = await signal_engine.extract_signals(symbol, "4h", key_levels)

    # Direction from 5m primary (scalp-optimized fallback chain)
    direction = signals_5m.get("raw_direction", 0)
    if direction == 0:
        direction = signals_15m.get("raw_direction", 0)
    if direction == 0:
        direction = signals_1h.get("raw_direction", 0)
    if direction == 0:
        direction = 1  # Default LONG if completely ambiguous

    # Unified score (6-layer scalp model)
    macro_data = macro_tracker.get_macro_data()
    result = scorer.compute_unified_score(
        signals_5m, signals_15m, signals_1h, signals_4h,
        symbol, macro_data, direction,
    )

    dir_str = result["direction"]
    score = result["score"]

    # Hysteresis: direction sticks unless new direction has score > threshold
    prev = _prev_direction.get(symbol)
    if prev and prev != dir_str and score < BIAS_THRESHOLD:
        dir_str = prev
    _prev_direction[symbol] = dir_str

    # Macro signals for display
    macro_signals = _score_macro_display(macro_data)
    all_signals = result["all_signals"]

    # Alerts
    macro_direction = _macro_direction(macro_data)
    alerts = _build_alerts(dir_str, macro_direction, all_signals, macro_signals, symbol)

    # Distance to key levels
    if current_price:
        for level in key_levels:
            if level.get("type") != "current":
                price = Decimal(level["price"])
                dist = ((price - current_price) / current_price * 100)
                level["distance_pct"] = str(round(dist, 2))

    # Timing coach — build temporary analysis-like dict for evaluation
    _timing_input = {
        "bias": {"direction": dir_str},
        "_signals_5m": signals_5m,
        "key_levels": [l for l in key_levels if l.get("type") != "current"],
        "current_price": str(current_price) if current_price else None,
    }
    timing = timing_coach.evaluate(_timing_input, symbol)

    return {
        "symbol": symbol,
        "bias": {
            "direction": dir_str,
            "confidence": score,
            "ta_score": round(result["raw_points"] / scorer.TOTAL_MAX, 3),
            "macro_score": round(result["layer_scores"]["macro"] / 5, 3),
            "layer_scores": result["layer_scores"],
        },
        "signals": {
            "technical": [signal_to_dict(s) for s in all_signals],
            "macro": [signal_to_dict(s) for s in macro_signals],
        },
        "key_levels": [l for l in key_levels if l.get("type") != "current"],
        "alerts": alerts,
        "current_price": str(current_price) if current_price else None,
        "timing": timing,
        "atr_5m": signals_5m.get("atr"),
        "atr_15m": signals_15m.get("atr"),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Macro signals for display ────────────────────────────────

def _score_macro_display(macro_data: dict) -> list[dict]:
    indicators = macro_data.get("indicators", {})
    if not indicators:
        return []

    signals = []

    dxy = indicators.get("dxy")
    if dxy:
        trend = dxy["trend"]
        change = abs(float(dxy.get("change_pct", 0)))
        magnitude = min(change / 1.0, 1.0)
        score = -magnitude if trend == "up" else (magnitude if trend == "down" else 0)
        signals.append({"name": "DXY", "value": dxy.get("value"),
                        "score": round(score, 2), "weight": 1.0, "trend": trend})

    vix = indicators.get("vix")
    if vix:
        val = float(vix.get("value", 0))
        if val > 30:
            score = -0.9
        elif val > 25:
            score = -0.7
        elif val > 20:
            score = -0.3
        elif val < 12:
            score = 1.0
        elif val < 15:
            score = 0.7
        else:
            score = 0
        signals.append({"name": "VIX", "value": vix.get("value"),
                        "score": score, "weight": 1.2, "trend": vix["trend"]})

    nasdaq = indicators.get("nasdaq")
    if nasdaq:
        trend = nasdaq["trend"]
        score = 0.5 if trend == "up" else (-0.5 if trend == "down" else 0)
        signals.append({"name": "Nasdaq", "value": nasdaq.get("value"),
                        "score": score, "weight": 0.8, "trend": trend})

    gold = indicators.get("gold")
    if gold:
        trend = gold["trend"]
        change = abs(float(gold.get("change_pct", 0)))
        magnitude = min(change / 2.0, 1.0)
        if trend == "up":
            score = -max(magnitude, 0.3)
        elif trend == "down":
            score = magnitude * 0.3
        else:
            score = 0
        signals.append({"name": "Gold", "value": gold.get("value"),
                        "score": round(score, 2), "weight": 0.8, "trend": trend})

    us10y = indicators.get("us10y")
    if us10y:
        trend = us10y["trend"]
        change = abs(float(us10y.get("change_pct", 0)))
        magnitude = min(change / 2.0, 1.0)
        score = -magnitude if trend == "up" else (magnitude if trend == "down" else 0)
        signals.append({"name": "US10Y", "value": us10y.get("value"),
                        "score": round(score, 2), "weight": 1.0, "trend": trend})

    spread = indicators.get("spread")
    if spread:
        val = float(spread.get("value", 0))
        if val < -0.5:
            score = -1.0
        elif val < 0:
            score = -0.7
        elif val < 0.2:
            score = -0.3
        elif val > 1.0:
            score = 0.3
        else:
            score = 0.1
        signals.append({"name": "Spread", "value": spread.get("value"),
                        "score": score, "weight": 0.8, "trend": spread.get("trend")})

    oil = indicators.get("oil")
    if oil:
        trend = oil["trend"]
        change = abs(float(oil.get("change_pct", 0)))
        magnitude = min(change / 2.0, 1.0)
        score = -magnitude * 0.5 if trend == "up" else (magnitude * 0.3 if trend == "down" else 0)
        signals.append({"name": "Oil", "value": oil.get("value"),
                        "score": round(score, 2), "weight": 0.6, "trend": trend})

    usdjpy = indicators.get("usdjpy")
    if usdjpy:
        trend = usdjpy["trend"]
        change = abs(float(usdjpy.get("change_pct", 0)))
        magnitude = min(change / 1.0, 1.0)
        score = -magnitude if trend == "down" else (magnitude * 0.3 if trend == "up" else 0)
        signals.append({"name": "USD/JPY", "value": usdjpy.get("value"),
                        "score": round(score, 2), "weight": 0.7, "trend": trend})

    return signals


def _macro_direction(macro_data: dict) -> str:
    indicators = macro_data.get("indicators", {})
    if not indicators:
        return "NEUTRAL"
    scores = []
    for ind in _score_macro_display(macro_data):
        scores.append(ind["score"])
    if not scores:
        return "NEUTRAL"
    avg = sum(scores) / len(scores)
    if avg > 0.15:
        return "LONG"
    if avg < -0.15:
        return "SHORT"
    return "NEUTRAL"


# ── Key levels ────────────────────────────────────────────────

async def _compute_key_levels(symbol: str) -> list[dict]:
    await kline_manager.fetch_and_store(symbol, "1d", limit=45)
    klines = await kline_manager.get_klines(symbol, "1d", limit=45)
    if not klines or len(klines) < 2:
        return []

    # Use last CLOSED daily candle for stable pivots (skip current forming candle)
    prev_day = klines[-2]
    high = Decimal(prev_day["high"])
    low = Decimal(prev_day["low"])
    close = Decimal(prev_day["close"])
    # Current price from the forming candle for above/below comparisons
    current_price = Decimal(klines[-1]["close"])

    pivot = (high + low + close) / 3
    rng = high - low
    s1 = 2 * pivot - high
    r1 = 2 * pivot - low
    s2 = pivot - rng
    r2 = pivot + rng
    s3 = low - 2 * (high - pivot)
    r3 = high + 2 * (pivot - low)

    levels = [
        {"price": str(round(r3, 2)), "type": "R3", "label": "Resistance extreme"},
        {"price": str(round(r2, 2)), "type": "R2", "label": "Forte resistance"},
        {"price": str(round(r1, 2)), "type": "R1", "label": "Resistance"},
        {"price": str(round(pivot, 2)), "type": "PP", "label": "Pivot du jour"},
        {"price": str(round(s1, 2)), "type": "S1", "label": "Support"},
        {"price": str(round(s2, 2)), "type": "S2", "label": "Fort support"},
        {"price": str(round(s3, 2)), "type": "S3", "label": "Support extreme"},
        {"price": str(round(close, 2)), "type": "PDC", "label": "Cloture veille"},
    ]

    # Session high/low (current forming daily candle)
    today = klines[-1]
    today_high = Decimal(today["high"])
    today_low = Decimal(today["low"])
    levels.append({"price": str(round(today_high, 2)), "type": "D_H", "label": "Plus haut du jour"})
    levels.append({"price": str(round(today_low, 2)), "type": "D_L", "label": "Plus bas du jour"})

    # Weekly pivot + high/low (from last closed 7 daily candles)
    if len(klines) >= 8:
        _add_weekly_levels(klines, levels)

    # VWAP (daily, from current forming candle using 1h klines)
    await _add_vwap_level(symbol, levels)

    # 4H swings — 150 candles (~25 days) for historical context
    await kline_manager.fetch_and_store(symbol, "4h", limit=150)
    klines_4h = await kline_manager.get_klines(symbol, "4h", limit=150)
    if klines_4h and len(klines_4h) >= 6:
        _detect_swings(klines_4h[:-1], levels)

    # 1H swings — 150 candles (~6 days) for scalp-level granularity + Fibonacci
    await kline_manager.fetch_and_store(symbol, "1h", limit=150)
    klines_1h = await kline_manager.get_klines(symbol, "1h", limit=150)
    if klines_1h and len(klines_1h) >= 6:
        _detect_swings(klines_1h[:-1], levels)

    # 15min swings — 144 candles (36h lookback) for intraday granularity
    await kline_manager.fetch_and_store(symbol, "15m", limit=144)
    klines_15m = await kline_manager.get_klines(symbol, "15m", limit=144)
    if klines_15m and len(klines_15m) >= 6:
        _detect_swings(klines_15m[:-1], levels)

    # Fibonacci retracements/extensions from latest significant 1H swing
    if klines_1h and len(klines_1h) >= 20:
        _add_fibonacci_levels(klines_1h, levels)

    # Psychological levels (round numbers near current price)
    _add_psychological_levels(current_price, levels)

    levels = _deduplicate_levels(levels)

    swing_types = {"SW_H", "SW_L"}
    fixed = [l for l in levels if l["type"] not in swing_types]
    swings_above = sorted(
        [l for l in levels if l["type"] in swing_types and Decimal(l["price"]) >= current_price],
        key=lambda x: Decimal(x["price"]),
    )[:4]
    swings_below = sorted(
        [l for l in levels if l["type"] in swing_types and Decimal(l["price"]) < current_price],
        key=lambda x: Decimal(x["price"]),
        reverse=True,
    )[:4]
    levels = fixed + swings_above + swings_below

    levels.sort(key=lambda x: Decimal(x["price"]), reverse=True)

    res_idx = 0
    sup_idx = 0
    for lvl in levels:
        if lvl["type"] not in ("SW_H", "SW_L"):
            continue
        price = Decimal(lvl["price"])
        if price >= current_price:
            res_idx += 1
            lvl["label"] = f"Plafond recent {res_idx}"
        else:
            sup_idx += 1
            lvl["label"] = f"Plancher recent {sup_idx}"

    levels.insert(0, {"current_price": current_price, "type": "current"})
    return levels


def _detect_swings(closed_klines: list[dict], levels: list[dict]):
    n = len(closed_klines)
    for i in range(2, n - 2):
        h = Decimal(closed_klines[i]["high"])
        lo = Decimal(closed_klines[i]["low"])
        left_h = max(Decimal(closed_klines[i - 1]["high"]),
                     Decimal(closed_klines[i - 2]["high"]))
        right_h = max(Decimal(closed_klines[i + 1]["high"]),
                      Decimal(closed_klines[i + 2]["high"]))
        left_l = min(Decimal(closed_klines[i - 1]["low"]),
                     Decimal(closed_klines[i - 2]["low"]))
        right_l = min(Decimal(closed_klines[i + 1]["low"]),
                      Decimal(closed_klines[i + 2]["low"]))

        if h > left_h and h > right_h:
            levels.append({"price": str(round(h, 2)),
                           "type": "SW_H", "label": "Plus haut"})
        if lo < left_l and lo < right_l:
            levels.append({"price": str(round(lo, 2)),
                           "type": "SW_L", "label": "Plus bas"})


def _deduplicate_levels(levels: list[dict]) -> list[dict]:
    if not levels:
        return levels
    result = []
    prices_seen = []
    priority = {
        "R3": 0, "R2": 1, "R1": 2, "PP": 3, "S1": 4, "S2": 5, "S3": 6,
        "PDC": 7, "VWAP": 8, "W_PP": 9, "W_H": 10, "W_L": 11,
        "D_H": 12, "D_L": 13,
        "FIB_618": 14, "FIB_50": 15, "FIB_382": 16,
        "FIB_1272": 17, "FIB_1618": 18, "PSYCH": 19,
        "SW_H": 20, "SW_L": 21,
    }
    levels.sort(key=lambda x: priority.get(x["type"], 99))
    for level in levels:
        price = Decimal(level["price"])
        too_close = False
        for seen in prices_seen:
            if seen != 0 and abs((price - seen) / seen) < Decimal("0.005"):
                too_close = True
                break
        if not too_close:
            result.append(level)
            prices_seen.append(price)
    return result


# ── Weekly levels ─────────────────────────────────────────────

def _add_weekly_levels(daily_klines: list[dict], levels: list[dict]):
    """Weekly pivot from last 7 closed daily candles."""
    closed = daily_klines[:-1]  # exclude forming candle
    if len(closed) < 7:
        return
    week = closed[-7:]
    w_high = max(Decimal(k["high"]) for k in week)
    w_low = min(Decimal(k["low"]) for k in week)
    w_close = Decimal(week[-1]["close"])
    w_pivot = (w_high + w_low + w_close) / 3

    levels.append({"price": str(round(w_pivot, 2)), "type": "W_PP", "label": "Pivot hebdo"})
    levels.append({"price": str(round(w_high, 2)), "type": "W_H", "label": "Plus haut hebdo"})
    levels.append({"price": str(round(w_low, 2)), "type": "W_L", "label": "Plus bas hebdo"})


# ── VWAP ─────────────────────────────────────────────────────

async def _add_vwap_level(symbol: str, levels: list[dict]):
    """VWAP from today's 1h klines (volume-weighted average price)."""
    klines_1h = await kline_manager.get_klines(symbol, "1h", limit=24)
    if not klines_1h or len(klines_1h) < 2:
        return
    # Use only candles from the current UTC day
    now = datetime.now(timezone.utc)
    today_iso = now.strftime("%Y-%m-%dT00:00:00")

    cum_vp = Decimal("0")
    cum_vol = Decimal("0")
    for k in klines_1h:
        if k["open_time"] < today_iso:
            continue
        typical = (Decimal(k["high"]) + Decimal(k["low"]) + Decimal(k["close"])) / 3
        vol = Decimal(k["volume"])
        cum_vp += typical * vol
        cum_vol += vol

    if cum_vol > 0:
        vwap = cum_vp / cum_vol
        levels.append({"price": str(round(vwap, 2)), "type": "VWAP", "label": "VWAP jour"})


# ── Fibonacci levels ──────────────────────────────────────────

_FIB_RATIOS = [
    (Decimal("0.382"), "FIB_382", "Fib 38.2%"),
    (Decimal("0.5"), "FIB_50", "Fib 50%"),
    (Decimal("0.618"), "FIB_618", "Fib 61.8%"),
]
_FIB_EXT_RATIOS = [
    (Decimal("0.272"), "FIB_1272", "Fib Ext 127.2%"),
    (Decimal("0.618"), "FIB_1618", "Fib Ext 161.8%"),
]


def _add_fibonacci_levels(klines_1h: list[dict], levels: list[dict]):
    swing = _find_significant_swing(klines_1h)
    if not swing:
        return
    swing_high, swing_low, is_bullish = swing
    diff = swing_high - swing_low

    if is_bullish:
        # Uptrend retracement: from high down toward low
        for ratio, ftype, label in _FIB_RATIOS:
            price = swing_high - diff * ratio
            levels.append({"price": str(round(price, 2)), "type": ftype, "label": label})
        # Extensions above the high
        for ratio, ftype, label in _FIB_EXT_RATIOS:
            price = swing_high + diff * ratio
            levels.append({"price": str(round(price, 2)), "type": ftype, "label": label})
    else:
        # Downtrend retracement: from low up toward high
        for ratio, ftype, label in _FIB_RATIOS:
            price = swing_low + diff * ratio
            levels.append({"price": str(round(price, 2)), "type": ftype, "label": label})
        # Extensions below the low
        for ratio, ftype, label in _FIB_EXT_RATIOS:
            price = swing_low - diff * ratio
            levels.append({"price": str(round(price, 2)), "type": ftype, "label": label})


def _find_significant_swing(klines: list[dict]) -> tuple[Decimal, Decimal, bool] | None:
    """Return (swing_high, swing_low, is_bullish) or None.

    is_bullish=True means low came BEFORE high (uptrend),
    is_bullish=False means high came BEFORE low (downtrend).
    """
    if len(klines) < 30:
        return None

    highs: list[tuple[int, Decimal]] = []
    lows: list[tuple[int, Decimal]] = []
    closed = klines[:-1]
    for i in range(1, len(closed) - 1):
        h = Decimal(closed[i]["high"])
        lo = Decimal(closed[i]["low"])
        prev_h = Decimal(closed[i - 1]["high"])
        next_h = Decimal(closed[i + 1]["high"])
        prev_l = Decimal(closed[i - 1]["low"])
        next_l = Decimal(closed[i + 1]["low"])
        if h > prev_h and h > next_h:
            highs.append((i, h))
        if lo < prev_l and lo < next_l:
            lows.append((i, lo))

    if not highs or not lows:
        return None

    for hi_idx, hi_price in reversed(highs):
        for lo_idx, lo_price in reversed(lows):
            if abs(hi_idx - lo_idx) < 10:
                continue
            diff = hi_price - lo_price
            if lo_price > 0 and diff / lo_price >= Decimal("0.01"):
                is_bullish = lo_idx < hi_idx
                return (hi_price, lo_price, is_bullish)

    return None


# ── Psychological levels ─────────────────────────────────────

def _add_psychological_levels(current_price: Decimal, levels: list[dict]):
    if current_price <= 0:
        return

    cp = current_price
    if cp >= 10000:
        step = Decimal("1000")
    elif cp >= 1000:
        step = Decimal("100")
    elif cp >= 100:
        step = Decimal("50")
    elif cp >= 10:
        step = Decimal("10")
    elif cp >= 1:
        step = Decimal("1")
    else:
        return

    base = (cp / step).to_integral_value() * step
    for m in range(-2, 4):
        psych = base + m * step
        if psych <= 0:
            continue
        dist_pct = abs(psych - cp) / cp * 100
        if dist_pct > 5:
            continue
        levels.append({
            "price": str(round(psych, 2)),
            "type": "PSYCH",
            "label": f"Niveau psycho {int(psych):,}".replace(",", " "),
        })


# ── Alerts ────────────────────────────────────────────────────

def _build_alerts(ta_dir: str, macro_dir: str, ta_signals: list, macro_signals: list, symbol: str = "") -> list[dict]:
    alerts = []

    if ta_dir != "NEUTRAL" and macro_dir != "NEUTRAL" and ta_dir != macro_dir:
        alerts.append({
            "type": "conflict",
            "severity": "warning",
            "message": f"AT dit {ta_dir} mais macro dit {macro_dir}",
        })
    elif ta_dir != "NEUTRAL" and ta_dir == macro_dir:
        alerts.append({
            "type": "aligned",
            "severity": "info",
            "message": f"AT et macro alignes : {ta_dir}",
        })

    if symbol:
        whales = whale_tracker.get_whale_alerts()
        for w in whales[:20]:
            if w["symbol"] == symbol:
                alerts.append({
                    "type": "whale",
                    "severity": "info",
                    "message": f"{format_qty(w['quote_qty'])} USDC {w['side'].lower()}",
                    "timestamp": w["timestamp"],
                })

    if symbol:
        ob = orderbook_tracker.get_orderbook_data(symbol)
        for w in ob.get("walls", []):
            dist = abs(w.get("distance_pct", 99))
            if 0.3 < dist < 2.0:
                side_label = "support" if w["side"] == "BID" else "resistance"
                alerts.append({
                    "type": "wall",
                    "severity": "info",
                    "message": f"Mur {side_label} a {w['price']} ({w['pct_of_total']}% du volume visible)",
                })

    return alerts
