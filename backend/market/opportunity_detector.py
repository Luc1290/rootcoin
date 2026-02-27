import asyncio
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import structlog

from backend.market import market_analyzer
from backend.trading import position_tracker
from backend.core.config import settings

log = structlog.get_logger()

_opportunities: deque[dict] = deque(maxlen=20)
_cooldowns: dict[str, datetime] = {}
_loop_task: asyncio.Task | None = None



async def start():
    global _loop_task
    _loop_task = asyncio.create_task(_run_loop())
    log.info("opportunity_detector_started")


async def stop():
    if _loop_task:
        _loop_task.cancel()
        try:
            await _loop_task
        except asyncio.CancelledError:
            pass
    log.info("opportunity_detector_stopped")


def get_opportunities() -> list[dict]:
    return list(_opportunities)


# ── Main loop ─────────────────────────────────────────────────

async def _run_loop():
    await asyncio.sleep(25)
    while True:
        try:
            _evaluate()
        except Exception:
            log.error("opportunity_check_failed", exc_info=True)
        await asyncio.sleep(settings.analysis_refresh_interval + 5)


def _evaluate():
    all_data = market_analyzer.get_all_analyses()
    if not all_data or not all_data.get("analyses"):
        return

    open_symbols = {
        p.symbol for p in position_tracker.get_positions() if p.is_active
    }
    now = datetime.now(timezone.utc)

    for analysis in all_data["analyses"]:
        symbol = analysis.get("symbol", "")
        if not symbol or symbol in open_symbols:
            continue

        cooldown_until = _cooldowns.get(symbol)
        if cooldown_until:
            elapsed = (now - cooldown_until).total_seconds()
            if elapsed < settings.opportunity_cooldown_minutes * 60:
                continue

        # Use unified score directly
        score = analysis.get("bias", {}).get("confidence", 0)
        if score < settings.opportunity_min_score:
            continue

        details = _extract_details(analysis)
        opp = _build_opportunity(analysis, score, details, now)
        _opportunities.appendleft(opp)
        _cooldowns[symbol] = now
        log.info(
            "opportunity_detected",
            symbol=symbol,
            score=score,
            direction=analysis["bias"]["direction"],
        )


# ── Details extraction (display only, no scoring) ────────────

def _extract_details(analysis: dict) -> dict:
    bias = analysis.get("bias", {})
    direction = bias.get("direction", "LONG")
    details: dict = {}

    # Signal agreement for display
    ta_signals = analysis.get("signals", {}).get("technical", [])
    agree = 0
    total = 0
    for sig in ta_signals:
        sc = sig.get("score", 0)
        if abs(sc) <= 0.15:
            continue
        total += 1
        if (direction == "LONG" and sc > 0.15) or (direction == "SHORT" and sc < -0.15):
            agree += 1
    details["signal_agreement"] = round(agree / total * 100) if total else 0

    # Best RSI
    best_rsi = None
    for sig in ta_signals:
        if sig.get("name", "").startswith("RSI"):
            best_rsi = sig.get("value")
            break
    details["best_rsi"] = best_rsi

    # Nearest level
    nearest = None
    best_dist = Decimal("99")
    for level in analysis.get("key_levels", []):
        try:
            raw_dist = Decimal(level.get("distance_pct", "99"))
        except (InvalidOperation, TypeError):
            continue
        if direction == "LONG" and raw_dist > Decimal("0"):
            continue
        if direction == "SHORT" and raw_dist < Decimal("0"):
            continue
        abs_dist = abs(raw_dist)
        if abs_dist < best_dist and abs_dist < Decimal("3"):
            best_dist = abs_dist
            nearest = level
    details["nearest_level"] = nearest

    # Flow details from layer scores
    layer_scores = bias.get("layer_scores", {})
    flow = layer_scores.get("flow", 0)
    details["has_buy_pressure"] = direction == "LONG" and flow > 3
    details["has_sell_pressure"] = direction == "SHORT" and flow > 3
    details["ob_confirming"] = flow > 8
    details["whale_confirming"] = flow >= 13

    return details


# ── Entry / TP / SL computation ───────────────────────────────

ATR_SL_BUFFER = Decimal("0.3")  # SL = level ± 0.3×ATR
ATR_FALLBACK_SL = Decimal("1.5")  # SL = entry ± 1.5×ATR if no level
RR_MIN = Decimal("1.5")  # Minimum R:R for TP1
RR_TP2 = Decimal("2.0")  # R:R for TP2 fallback


def _compute_levels(analysis: dict, direction: str) -> dict:
    try:
        entry = Decimal(analysis.get("current_price", "0"))
    except InvalidOperation:
        return {}
    if entry <= 0:
        return {}

    atr_raw = analysis.get("atr_15m")
    atr = Decimal(str(atr_raw)) if atr_raw and atr_raw > 0 else None
    key_levels = analysis.get("key_levels", [])

    # Collect supports (below price) and resistances (above price)
    supports = []
    resistances = []
    for level in key_levels:
        try:
            price = Decimal(level["price"])
            dist = Decimal(level.get("distance_pct", "99"))
        except (InvalidOperation, KeyError, TypeError):
            continue
        if dist < 0:
            supports.append(price)
        elif dist > 0:
            resistances.append(price)

    supports.sort(reverse=True)  # Closest support first
    resistances.sort()  # Closest resistance first

    if direction == "LONG":
        sl = _pick_sl_long(entry, supports, atr)
        tp1 = _pick_tp_long(entry, sl, resistances, atr)
    else:
        sl = _pick_sl_short(entry, resistances, atr)
        tp1 = _pick_tp_short(entry, sl, supports, atr)

    risk = abs(entry - sl)
    if risk == 0:
        return {}

    reward = abs(tp1 - entry)
    rr = reward / risk

    # TP2 if TP1 R:R is modest
    tp2 = None
    if direction == "LONG":
        if rr < RR_MIN and len(resistances) >= 2:
            tp2 = resistances[1]
        elif rr < RR_MIN:
            tp2 = entry + risk * RR_TP2
    else:
        if rr < RR_MIN and len(supports) >= 2:
            tp2 = supports[1]
        elif rr < RR_MIN:
            tp2 = entry - risk * RR_TP2

    result = {
        "entry": str(round(entry, _price_precision(entry))),
        "sl": str(round(sl, _price_precision(sl))),
        "tp1": str(round(tp1, _price_precision(tp1))),
        "rr": str(round(rr, 2)),
    }
    if tp2 is not None:
        result["tp2"] = str(round(tp2, _price_precision(tp2)))
    return result


def _pick_sl_long(entry: Decimal, supports: list[Decimal], atr: Decimal | None) -> Decimal:
    if supports:
        buffer = atr * ATR_SL_BUFFER if atr else supports[0] * Decimal("0.002")
        return supports[0] - buffer
    if atr:
        return entry - atr * ATR_FALLBACK_SL
    return entry * Decimal("0.985")  # 1.5% fallback


def _pick_tp_long(entry: Decimal, sl: Decimal, resistances: list[Decimal], atr: Decimal | None) -> Decimal:
    risk = entry - sl
    if resistances:
        return resistances[0]
    return entry + risk * RR_MIN


def _pick_sl_short(entry: Decimal, resistances: list[Decimal], atr: Decimal | None) -> Decimal:
    if resistances:
        buffer = atr * ATR_SL_BUFFER if atr else resistances[0] * Decimal("0.002")
        return resistances[0] + buffer
    if atr:
        return entry + atr * ATR_FALLBACK_SL
    return entry * Decimal("1.015")


def _pick_tp_short(entry: Decimal, sl: Decimal, supports: list[Decimal], atr: Decimal | None) -> Decimal:
    risk = sl - entry
    if supports:
        return supports[0]
    return entry - risk * RR_MIN


def _price_precision(price: Decimal) -> int:
    if price >= 1000:
        return 2
    if price >= 1:
        return 4
    return 6


# ── Message generation ────────────────────────────────────────

def _build_opportunity(analysis: dict, score: float, details: dict, now: datetime) -> dict:
    bias = analysis["bias"]
    symbol = analysis["symbol"]
    direction = bias["direction"]
    confidence = bias["confidence"]
    message = _build_message(symbol, direction, confidence, details)
    key_signals = _extract_key_signals(direction, confidence, details)
    ts = int(now.timestamp())

    levels = _compute_levels(analysis, direction)

    return {
        "id": f"{symbol}_{ts}",
        "symbol": symbol,
        "direction": direction,
        "confidence": confidence,
        "score": round(score),
        "current_price": analysis.get("current_price", ""),
        "message": message,
        "key_signals": key_signals,
        "nearest_level": details.get("nearest_level"),
        "levels": levels,
        "detected_at": now.isoformat(),
    }


def _build_message(symbol: str, direction: str, confidence: int, details: dict) -> str:
    short = symbol.replace("USDC", "")
    parts = []

    level = details.get("nearest_level")
    if level:
        price_str = _fmt_price(level.get("price", ""))
        if direction == "LONG":
            parts.append(f"{short} proche du support {price_str}")
        else:
            parts.append(f"{short} sous la resistance {price_str}")
    else:
        parts.append(short)

    rsi = details.get("best_rsi")
    if rsi is not None:
        parts.append(f"RSI a {rsi:.0f}")

    if details.get("has_buy_pressure"):
        parts.append("volume acheteur en hausse")
    elif details.get("has_sell_pressure"):
        parts.append("pression vendeuse")

    agreement = details.get("signal_agreement", 0)
    if agreement >= 70:
        parts.append(f"{agreement}% des signaux alignes")

    if details.get("ob_confirming"):
        parts.append("orderbook favorable")

    if details.get("whale_confirming"):
        parts.append("whale recente")

    bias_label = "bullish" if direction == "LONG" else "bearish"
    sig_str = ", ".join(parts)

    if direction == "LONG":
        return f"Hey \u2014 {sig_str}, biais {bias_label} ({confidence}%). \u00c7a vaut un coup d'\u0153il ?"
    return f"Hey \u2014 {sig_str}, biais {bias_label} ({confidence}%). \u00c0 surveiller."


def _extract_key_signals(direction: str, confidence: int, details: dict) -> list[dict]:
    signals = []
    agreement = details.get("signal_agreement", 0)
    if agreement >= 60:
        stype = "bullish" if direction == "LONG" else "bearish"
        signals.append({"label": f"{agreement}% align\u00e9s", "type": stype})

    rsi = details.get("best_rsi")
    if rsi is not None:
        stype = "bullish" if direction == "LONG" else "bearish"
        signals.append({"label": f"RSI {rsi:.0f}", "type": stype})

    level = details.get("nearest_level")
    if level:
        price_str = _fmt_price(level.get("price", ""))
        if direction == "LONG":
            signals.append({"label": f"Support {price_str}", "type": "level"})
        else:
            signals.append({"label": f"R\u00e9sistance {price_str}", "type": "level"})

    if details.get("has_buy_pressure") or details.get("has_sell_pressure"):
        label = "Volume +" if details.get("has_buy_pressure") else "Volume -"
        stype = "bullish" if details.get("has_buy_pressure") else "bearish"
        signals.append({"label": label, "type": stype})

    if details.get("ob_confirming"):
        signals.append({"label": "Orderbook", "type": "bullish" if direction == "LONG" else "bearish"})

    if details.get("whale_confirming"):
        signals.append({"label": "Whale", "type": "bullish" if direction == "LONG" else "bearish"})

    return signals[:4]


def _fmt_price(price_str: str) -> str:
    try:
        n = float(price_str)
    except (ValueError, TypeError):
        return price_str
    if n >= 1000:
        return f"${n:,.0f}".replace(",", " ")
    if n >= 1:
        return f"${n:.2f}"
    return f"${n:.4f}"
