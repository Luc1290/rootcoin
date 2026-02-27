import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from backend.market import kline_manager, macro_tracker, orderbook_tracker, whale_tracker
from backend.market.analysis_formatter import build_justification, signal_to_dict, format_qty, TIMEFRAMES
from backend.scoring import signal_engine, scorer
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
    log.info("analysis_refreshed", symbols=len(symbols))


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
    signals_15m = await signal_engine.extract_signals(symbol, "15m", key_levels)
    signals_1h = await signal_engine.extract_signals(symbol, "1h", key_levels)
    signals_4h = await signal_engine.extract_signals(symbol, "4h", key_levels)

    # Direction from 15m primary
    direction = signals_15m.get("raw_direction", 0)
    if direction == 0:
        direction = signals_1h.get("raw_direction", 0)
    if direction == 0:
        direction = 1  # Default LONG if completely ambiguous

    # Unified score
    macro_data = macro_tracker.get_macro_data()
    result = scorer.compute_unified_score(
        signals_15m, signals_1h, signals_4h,
        symbol, macro_data, direction,
    )

    dir_str = result["direction"]
    score = result["score"]

    # Hysteresis: direction sticks unless new direction has score > threshold
    prev = _prev_direction.get(symbol)
    if prev and prev != dir_str and score < BIAS_THRESHOLD:
        dir_str = prev
    _prev_direction[symbol] = dir_str

    # Macro signals for display/justification
    macro_signals = _score_macro_display(macro_data)
    all_signals = result["all_signals"]

    # Justification
    justification = build_justification(all_signals, macro_signals, dir_str)

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

    return {
        "symbol": symbol,
        "bias": {
            "direction": dir_str,
            "confidence": score,
            "justification": justification,
            "ta_score": round(result["raw_points"] / scorer.TOTAL_MAX, 3),
            "macro_score": round(result["layer_scores"]["macro"] / 10, 3),
            "layer_scores": result["layer_scores"],
        },
        "signals": {
            "technical": [signal_to_dict(s) for s in all_signals],
            "macro": [signal_to_dict(s) for s in macro_signals],
        },
        "key_levels": [l for l in key_levels if l.get("type") != "current"],
        "alerts": alerts,
        "current_price": str(current_price) if current_price else None,
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
    await kline_manager.fetch_and_store(symbol, "1d", limit=30)
    klines = await kline_manager.get_klines(symbol, "1d", limit=30)
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

    await kline_manager.fetch_and_store(symbol, "4h", limit=30)
    klines_4h = await kline_manager.get_klines(symbol, "4h", limit=30)
    if klines_4h and len(klines_4h) >= 6:
        # Exclude last candle (still forming) — use only closed candles
        closed_4h = klines_4h[:-1]
        for i in range(1, len(closed_4h) - 1):
            h = float(closed_4h[i]["high"])
            l = float(closed_4h[i]["low"])
            prev_h = float(closed_4h[i - 1]["high"])
            next_h = float(closed_4h[i + 1]["high"])
            prev_l = float(closed_4h[i - 1]["low"])
            next_l = float(closed_4h[i + 1]["low"])

            if h > prev_h and h > next_h:
                levels.append({"price": str(round(Decimal(str(h)), 2)),
                               "type": "SW_H", "label": "Plus haut"})
            if l < prev_l and l < next_l:
                levels.append({"price": str(round(Decimal(str(l)), 2)),
                               "type": "SW_L", "label": "Plus bas"})

    levels = _deduplicate_levels(levels)

    pivots = [l for l in levels if l["type"] not in ("SW_H", "SW_L")]
    swings_above = sorted(
        [l for l in levels if l["type"] in ("SW_H", "SW_L") and Decimal(l["price"]) >= current_price],
        key=lambda x: Decimal(x["price"]),
    )[:2]
    swings_below = sorted(
        [l for l in levels if l["type"] in ("SW_H", "SW_L") and Decimal(l["price"]) < current_price],
        key=lambda x: Decimal(x["price"]),
        reverse=True,
    )[:2]
    levels = pivots + swings_above + swings_below

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


def _deduplicate_levels(levels: list[dict]) -> list[dict]:
    if not levels:
        return levels
    result = []
    prices_seen = []
    priority = {"R2": 0, "R1": 1, "PP": 2, "S1": 3, "S2": 4, "SW_H": 5, "SW_L": 6}
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
