import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from backend.market import kline_manager, macro_tracker, orderbook_tracker, whale_tracker
from backend.market.analysis_formatter import build_justification, signal_to_dict, format_qty, TIMEFRAMES
from backend.trading import position_tracker
from backend.core.config import settings

log = structlog.get_logger()

STALE_THRESHOLD = 900

# Signal weights
WEIGHTS = {
    "rsi": 1.0,
    "macd": 1.5,
    "ma_cross": 1.0,
    "bollinger": 0.5,
    "mfi": 0.5,
    "stoch_rsi": 0.8,
    "buy_sell": 0.8,
    "obv": 0.5,
    "orderbook": 0.6,
}

MACRO_WEIGHT = 0.3
TA_WEIGHT = 0.7
BIAS_THRESHOLD = 0.15

TIMEFRAME_WEIGHTS = {"15m": 0.7, "1h": 1.0, "4h": 1.3}

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
    await asyncio.sleep(15)  # Let other services init
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
    log.info("analysis_refreshed", symbols=len(symbols))


def _get_symbols() -> list[str]:
    symbols = set(settings.watchlist)
    for pos in position_tracker.get_positions():
        symbols.add(pos.symbol)
    return sorted(symbols)


# ── Per-symbol analysis ───────────────────────────────────────

async def _analyze_symbol(symbol: str) -> dict:
    # Fetch klines for all timeframes
    ta_signals = []
    for tf in TIMEFRAMES:
        signals = await _score_timeframe(symbol, tf)
        ta_signals.extend(signals)

    # Key levels from daily klines
    key_levels = await _compute_key_levels(symbol)

    # Current price from latest kline
    current_price = None
    if key_levels:
        current_price = key_levels[0].get("current_price")

    # TA score
    ta_score = _weighted_score(ta_signals)

    # Macro score
    macro_data = macro_tracker.get_macro_data()
    macro_signals = _score_macro(macro_data)
    macro_score = _weighted_score(macro_signals) if macro_signals else 0

    # Final bias
    final_score = TA_WEIGHT * ta_score + MACRO_WEIGHT * macro_score
    # Power 0.65 scaling: less compressed than sqrt, allows higher confidence
    # |0.05| -> 11%, |0.15| -> 24%, |0.30| -> 40%, |0.50| -> 57%, |1.0| -> 100%
    confidence = round(min(abs(final_score) ** 0.65 * 100, 100))

    prev = _prev_direction.get(symbol)
    if prev == "LONG" and final_score >= -BIAS_THRESHOLD:
        direction = "LONG"
    elif prev == "SHORT" and final_score <= BIAS_THRESHOLD:
        direction = "SHORT"
    else:
        direction = "LONG" if final_score >= 0 else "SHORT"
    _prev_direction[symbol] = direction

    # Conflict detection
    ta_direction = "LONG" if ta_score > BIAS_THRESHOLD else ("SHORT" if ta_score < -BIAS_THRESHOLD else "NEUTRAL")
    macro_direction = "LONG" if macro_score > BIAS_THRESHOLD else ("SHORT" if macro_score < -BIAS_THRESHOLD else "NEUTRAL")
    alerts = _build_alerts(ta_direction, macro_direction, ta_signals, macro_signals, symbol)

    # Justification
    justification = build_justification(ta_signals, macro_signals, direction)

    # Add distance_pct to key levels
    if current_price:
        for level in key_levels:
            if level.get("type") != "current":
                price = Decimal(level["price"])
                dist = ((price - current_price) / current_price * 100)
                level["distance_pct"] = str(round(dist, 2))

    return {
        "symbol": symbol,
        "bias": {
            "direction": direction,
            "confidence": confidence,
            "justification": justification,
            "ta_score": round(ta_score, 3),
            "macro_score": round(macro_score, 3),
        },
        "signals": {
            "technical": [signal_to_dict(s) for s in ta_signals],
            "macro": [signal_to_dict(s) for s in macro_signals],
        },
        "key_levels": [l for l in key_levels if l.get("type") != "current"],
        "alerts": alerts,
        "current_price": str(current_price) if current_price else None,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── TA scoring ────────────────────────────────────────────────

async def _score_timeframe(symbol: str, interval: str) -> list[dict]:
    await kline_manager.fetch_and_store(symbol, interval, limit=200)
    klines = await kline_manager.get_klines(symbol, interval, limit=200)
    if len(klines) < 30:
        return []

    indicators = kline_manager.compute_indicators(
        klines,
        {"rsi", "macd", "ma", "bb", "mfi", "stoch_rsi", "buy_sell", "obv", "adx"},
    )

    signals = []
    n = len(klines) - 1  # Last candle index
    close = float(klines[n]["close"])
    tf_mult = TIMEFRAME_WEIGHTS.get(interval, 1.0)

    # ADX multiplier
    adx_val = _last_valid(indicators.get("adx", []))
    adx_mult = 1.0
    if adx_val is not None:
        if adx_val < 20:
            adx_mult = 0.6  # Ranging, dampen signals
        elif adx_val > 30:
            adx_mult = 1.3  # Strong trend, amplify

    # RSI
    rsi = _last_valid(indicators.get("rsi", []))
    if rsi is not None:
        if rsi < 30:
            score = 1.0
        elif rsi < 40:
            score = 0.5
        elif rsi > 70:
            score = -1.0
        elif rsi > 60:
            score = -0.5
        else:
            score = 0
        signals.append({"name": f"RSI({interval})", "value": rsi,
                        "score": score * adx_mult, "weight": WEIGHTS["rsi"] * tf_mult})

    # MACD
    macd_hist = _last_valid(indicators.get("macd_hist", []))
    macd_hist_prev = _nth_valid(indicators.get("macd_hist", []), -2)
    if macd_hist is not None:
        if macd_hist > 0 and (macd_hist_prev is not None and macd_hist > macd_hist_prev):
            score = 1.0
        elif macd_hist > 0:
            score = 0.5
        elif macd_hist < 0 and (macd_hist_prev is not None and macd_hist < macd_hist_prev):
            score = -1.0
        elif macd_hist < 0:
            score = -0.5
        else:
            score = 0
        signals.append({"name": f"MACD({interval})", "value": round(macd_hist, 4),
                        "score": score * adx_mult, "weight": WEIGHTS["macd"] * tf_mult})

    # MA cross
    ma7 = _last_valid(indicators.get("ma_7", []))
    ma25 = _last_valid(indicators.get("ma_25", []))
    if ma7 is not None and ma25 is not None:
        if close > ma25 and ma7 > ma25:
            score = 1.0
        elif close < ma25 and ma7 < ma25:
            score = -1.0
        else:
            score = 0
        signals.append({"name": f"MA({interval})", "value": round(ma7, 2),
                        "score": score * adx_mult, "weight": WEIGHTS["ma_cross"] * tf_mult})

    # Bollinger
    bb_upper = _last_valid(indicators.get("bb_upper", []))
    bb_lower = _last_valid(indicators.get("bb_lower", []))
    bb_mid = _last_valid(indicators.get("bb_mid", []))
    if bb_upper and bb_lower and bb_mid:
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            position_in_band = (close - bb_lower) / bb_range
            if position_in_band < 0.2:
                score = 1.0
            elif position_in_band < 0.35:
                score = 0.5
            elif position_in_band > 0.8:
                score = -1.0
            elif position_in_band > 0.65:
                score = -0.5
            else:
                score = 0
            signals.append({"name": f"BB({interval})", "value": round(position_in_band, 2),
                            "score": score * adx_mult, "weight": WEIGHTS["bollinger"] * tf_mult})

    # MFI
    mfi = _last_valid(indicators.get("mfi", []))
    if mfi is not None:
        if mfi < 20:
            score = 1.0
        elif mfi < 35:
            score = 0.5
        elif mfi > 80:
            score = -1.0
        elif mfi > 65:
            score = -0.5
        else:
            score = 0
        signals.append({"name": f"MFI({interval})", "value": mfi,
                        "score": score, "weight": WEIGHTS["mfi"] * tf_mult})

    # StochRSI
    stoch_k = _last_valid(indicators.get("stoch_rsi_k", []))
    stoch_d = _last_valid(indicators.get("stoch_rsi_d", []))
    if stoch_k is not None and stoch_d is not None:
        if stoch_k < 20 and stoch_k > stoch_d:
            score = 1.0
        elif stoch_k < 30:
            score = 0.5
        elif stoch_k > 80 and stoch_k < stoch_d:
            score = -1.0
        elif stoch_k > 70:
            score = -0.5
        else:
            score = 0
        signals.append({"name": f"StochRSI({interval})", "value": round(stoch_k, 1),
                        "score": score * adx_mult, "weight": WEIGHTS["stoch_rsi"] * tf_mult})

    # Buy/Sell pressure
    bs = _last_valid(indicators.get("buy_sell", []))
    if bs is not None:
        if bs > 5:
            score = min(bs / 15, 1.0)
        elif bs < -5:
            score = max(bs / 15, -1.0)
        else:
            score = 0
        signals.append({"name": f"B/S({interval})", "value": round(bs, 1),
                        "score": round(score, 2), "weight": WEIGHTS["buy_sell"] * tf_mult})

    # OBV trend (compare last 5 OBV values direction vs price direction)
    obv_list = indicators.get("obv", [])
    if len(obv_list) >= 5:
        obv_recent = obv_list[-5:]
        closes_recent = [float(klines[i]["close"]) for i in range(n - 4, n + 1)]
        obv_up = obv_recent[-1] > obv_recent[0] if all(v is not None for v in obv_recent) else None
        price_up = closes_recent[-1] > closes_recent[0]
        if obv_up is not None:
            if obv_up and price_up:
                score = 0.5  # Confirmation
            elif not obv_up and not price_up:
                score = -0.5
            elif obv_up and not price_up:
                score = 0.8  # Bullish divergence
            else:
                score = -0.8  # Bearish divergence
            signals.append({"name": f"OBV({interval})", "value": None,
                            "score": score, "weight": WEIGHTS["obv"] * tf_mult})

    # Orderbook imbalance (only on 1h to avoid tripling the weight)
    if interval == "1h":
        ob_imbalance = orderbook_tracker.get_imbalance(symbol)
        if ob_imbalance is not None and abs(ob_imbalance) > 0.15:
            if abs(ob_imbalance) > 0.3:
                sign = 1.0 if ob_imbalance > 0 else -1.0
                score = sign * min(abs(ob_imbalance) / 0.6 * 0.75, 1.0)
            else:
                score = ob_imbalance * 1.5
            signals.append({"name": "OB_Imbalance", "value": round(ob_imbalance, 3),
                            "score": round(score, 2), "weight": WEIGHTS["orderbook"]})

    # Trend-aware oscillator dampening: when ADX shows a trend and
    # MACD+MA agree on direction, cut the weight of oscillators that
    # disagree (mean-reversion signals fighting a clear trend).
    if adx_val is not None and adx_val > 25:
        trend_scores = [s["score"] for s in signals if s["name"].startswith(("MACD", "MA("))]
        if len(trend_scores) >= 2:
            avg_trend = sum(trend_scores) / len(trend_scores)
            if abs(avg_trend) > 0.4:  # trend indicators clearly agree
                osc_names = ("RSI", "BB", "MFI", "StochRSI")
                for s in signals:
                    if s["name"].startswith(osc_names):
                        # oscillator opposes trend direction
                        if (avg_trend > 0 and s["score"] < -0.2) or (avg_trend < 0 and s["score"] > 0.2):
                            s["weight"] *= 0.3

    return signals


# ── Macro scoring ─────────────────────────────────────────────

def _score_macro(macro_data: dict) -> list[dict]:
    indicators = macro_data.get("indicators", {})
    if not indicators:
        return []

    signals = []

    # DXY: up = bearish for crypto
    dxy = indicators.get("dxy")
    if dxy:
        trend = dxy["trend"]
        change = abs(float(dxy.get("change_pct", 0)))
        magnitude = min(change / 1.0, 1.0)  # Normalize: 1% change = full signal
        if trend == "up":
            score = -magnitude
        elif trend == "down":
            score = magnitude
        else:
            score = 0
        signals.append({"name": "DXY", "value": dxy.get("value"),
                        "score": round(score, 2), "weight": 1.0, "trend": trend})

    # VIX: high = bearish (risk-off)
    # Ranges: <12 extreme greed, 12-15 low fear, 15-20 normal, 20-30 elevated, 30-50 panic, 50+ crash
    vix = indicators.get("vix")
    if vix:
        val = float(vix.get("value", 0))
        if val > 50:
            score = -1.0
        elif val > 30:
            score = -0.9
        elif val > 25:
            score = -0.7
        elif val > 20:
            score = -0.3
        elif val < 12:
            score = 1.0
        elif val < 15:
            score = 0.7
        elif val < 18:
            score = 0.3
        else:
            score = 0
        signals.append({"name": "VIX", "value": vix.get("value"),
                        "score": score, "weight": 1.2, "trend": vix["trend"]})

    # Nasdaq: up = bullish (risk-on, high correlation with crypto)
    nasdaq = indicators.get("nasdaq")
    if nasdaq:
        trend = nasdaq["trend"]
        if trend == "up":
            score = 0.5
        elif trend == "down":
            score = -0.5
        else:
            score = 0
        signals.append({"name": "Nasdaq", "value": nasdaq.get("value"),
                        "score": score, "weight": 0.8, "trend": trend})

    # Gold: up = risk-off = bearish crypto, scaled by magnitude
    gold = indicators.get("gold")
    if gold:
        gold_trend = gold["trend"]
        change = abs(float(gold.get("change_pct", 0)))
        magnitude = min(change / 2.0, 1.0)  # 2% change = full signal
        if gold_trend == "up":
            score = -max(magnitude, 0.3)  # Always bearish, floor at -0.3
        elif gold_trend == "down":
            score = magnitude * 0.3  # Mildly bullish (selling safe haven)
        else:
            score = 0
        signals.append({"name": "Gold", "value": gold.get("value"),
                        "score": round(score, 2), "weight": 0.8, "trend": gold_trend})

    # US10Y: rising yields = bearish (liquidity drain from risk assets)
    us10y = indicators.get("us10y")
    if us10y:
        trend = us10y["trend"]
        change = abs(float(us10y.get("change_pct", 0)))
        magnitude = min(change / 2.0, 1.0)
        if trend == "up":
            score = -magnitude
        elif trend == "down":
            score = magnitude
        else:
            score = 0
        signals.append({"name": "US10Y", "value": us10y.get("value"),
                        "score": round(score, 2), "weight": 1.0, "trend": trend})

    # Spread (10Y-5Y): inverted curve = recession signal = bearish
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

    # Oil: rising = inflation = hawkish Fed = bearish crypto
    oil = indicators.get("oil")
    if oil:
        trend = oil["trend"]
        change = abs(float(oil.get("change_pct", 0)))
        magnitude = min(change / 2.0, 1.0)
        if trend == "up":
            score = -magnitude * 0.5
        elif trend == "down":
            score = magnitude * 0.3
        else:
            score = 0
        signals.append({"name": "Oil", "value": oil.get("value"),
                        "score": round(score, 2), "weight": 0.6, "trend": trend})

    # USDJPY: falling = yen strengthening = carry trade unwind = bearish
    usdjpy = indicators.get("usdjpy")
    if usdjpy:
        trend = usdjpy["trend"]
        change = abs(float(usdjpy.get("change_pct", 0)))
        magnitude = min(change / 1.0, 1.0)
        if trend == "down":
            score = -magnitude
        elif trend == "up":
            score = magnitude * 0.3
        else:
            score = 0
        signals.append({"name": "USD/JPY", "value": usdjpy.get("value"),
                        "score": round(score, 2), "weight": 0.7, "trend": trend})

    return signals


# ── Key levels ────────────────────────────────────────────────

async def _compute_key_levels(symbol: str) -> list[dict]:
    await kline_manager.fetch_and_store(symbol, "1d", limit=30)
    klines = await kline_manager.get_klines(symbol, "1d", limit=30)
    if not klines:
        return []

    last = klines[-1]
    high = Decimal(last["high"])
    low = Decimal(last["low"])
    close = Decimal(last["close"])

    # Classic pivot points
    pivot = (high + low + close) / 3
    s1 = 2 * pivot - high
    r1 = 2 * pivot - low
    s2 = pivot - (high - low)
    r2 = pivot + (high - low)

    levels = [
        {"price": str(round(r2, 2)), "type": "R2", "label": "Forte resistance"},
        {"price": str(round(r1, 2)), "type": "R1", "label": "Resistance"},
        {"price": str(round(pivot, 2)), "type": "PP", "label": "Pivot du jour"},
        {"price": str(round(s1, 2)), "type": "S1", "label": "Support"},
        {"price": str(round(s2, 2)), "type": "S2", "label": "Fort support"},
    ]

    # Swing highs/lows from 4h candles (~5 days)
    await kline_manager.fetch_and_store(symbol, "4h", limit=30)
    klines_4h = await kline_manager.get_klines(symbol, "4h", limit=30)
    if klines_4h and len(klines_4h) >= 5:
        for i in range(1, len(klines_4h) - 1):
            h = float(klines_4h[i]["high"])
            l = float(klines_4h[i]["low"])
            prev_h = float(klines_4h[i - 1]["high"])
            next_h = float(klines_4h[i + 1]["high"])
            prev_l = float(klines_4h[i - 1]["low"])
            next_l = float(klines_4h[i + 1]["low"])

            if h > prev_h and h > next_h:
                levels.append({"price": str(round(Decimal(str(h)), 2)),
                               "type": "SW_H", "label": "Plus haut"})
            if l < prev_l and l < next_l:
                levels.append({"price": str(round(Decimal(str(l)), 2)),
                               "type": "SW_L", "label": "Plus bas"})

    # Deduplicate close levels (within 0.3%)
    levels = _deduplicate_levels(levels)

    # Keep only the 2 closest swings above and 2 below current price
    pivots = [l for l in levels if l["type"] not in ("SW_H", "SW_L")]
    swings_above = sorted(
        [l for l in levels if l["type"] in ("SW_H", "SW_L") and Decimal(l["price"]) >= close],
        key=lambda x: Decimal(x["price"]),
    )[:2]
    swings_below = sorted(
        [l for l in levels if l["type"] in ("SW_H", "SW_L") and Decimal(l["price"]) < close],
        key=lambda x: Decimal(x["price"]),
        reverse=True,
    )[:2]
    levels = pivots + swings_above + swings_below

    # Sort by price descending
    levels.sort(key=lambda x: Decimal(x["price"]), reverse=True)

    # Relabel swings based on position vs current price
    res_idx = 0
    sup_idx = 0
    for lvl in levels:
        if lvl["type"] not in ("SW_H", "SW_L"):
            continue
        price = Decimal(lvl["price"])
        if price >= close:
            res_idx += 1
            lvl["label"] = f"Plafond recent {res_idx}"
        else:
            sup_idx += 1
            lvl["label"] = f"Plancher recent {sup_idx}"

    # Attach current price for reference
    levels.insert(0, {"current_price": close, "type": "current"})

    return levels


def _deduplicate_levels(levels: list[dict]) -> list[dict]:
    if not levels:
        return levels
    result = []
    prices_seen = []
    # Prioritize pivot types over swing
    priority = {"R2": 0, "R1": 1, "PP": 2, "S1": 3, "S2": 4, "SW_H": 5, "SW_L": 6}
    levels.sort(key=lambda x: priority.get(x["type"], 99))
    for level in levels:
        price = Decimal(level["price"])
        too_close = False
        for seen in prices_seen:
            if seen != 0 and abs((price - seen) / seen) < Decimal("0.003"):
                too_close = True
                break
        if not too_close:
            result.append(level)
            prices_seen.append(price)
    return result


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

    # Whale alerts (filtered to current symbol)
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

    # Orderbook wall alerts
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


# ── Helpers ───────────────────────────────────────────────────

def _weighted_score(signals: list[dict]) -> float:
    if not signals:
        return 0
    total_weight = sum(s["weight"] for s in signals)
    if total_weight == 0:
        return 0
    return sum(s["score"] * s["weight"] for s in signals) / total_weight


def _last_valid(data: list) -> float | None:
    for v in reversed(data):
        if v is not None:
            return v
    return None


def _nth_valid(data: list, offset: int) -> float | None:
    valid = [v for v in data if v is not None]
    if not valid:
        return None
    try:
        return valid[offset]
    except IndexError:
        return None


