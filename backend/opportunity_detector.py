import asyncio
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import structlog

from backend import market_analyzer, orderbook_tracker, position_tracker, whale_tracker
from backend.config import settings

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

        score, details = _score_opportunity(analysis, symbol, now)
        if score >= settings.opportunity_min_score:
            opp = _build_opportunity(analysis, score, details, now)
            _opportunities.appendleft(opp)
            _cooldowns[symbol] = now
            log.info(
                "opportunity_detected",
                symbol=symbol,
                score=score,
                direction=analysis["bias"]["direction"],
            )


# ── Scoring ───────────────────────────────────────────────────

def _score_opportunity(analysis: dict, symbol: str, now: datetime) -> tuple[float, dict]:
    bias = analysis.get("bias", {})
    direction = bias.get("direction", "")
    confidence = bias.get("confidence", 0)
    details: dict = {}

    # Hard gate: skip if confidence < 30
    if confidence < 30:
        return 0, details

    # 1. Bias confidence = anchor (max 50)
    #    30% -> 0 pts, 60% -> 21 pts, 80% -> 36 pts, 100% -> 50 pts
    bias_pts = min((confidence - 30) / 70 * 50, 50)

    # 2. Signal agreement (max 15) — what % of TA signals agree with direction
    ta_signals = analysis.get("signals", {}).get("technical", [])
    agree = 0
    disagree = 0
    for sig in ta_signals:
        sc = sig.get("score", 0)
        if direction == "LONG":
            if sc > 0.15:
                agree += 1
            elif sc < -0.15:
                disagree += 1
        elif direction == "SHORT":
            if sc < -0.15:
                agree += 1
            elif sc > 0.15:
                disagree += 1
    total_opinionated = agree + disagree
    if total_opinionated > 0:
        agreement_ratio = agree / total_opinionated
        agreement_pts = agreement_ratio * 15
    else:
        agreement_pts = 0.0
    details["signal_agreement"] = round(agreement_ratio * 100) if total_opinionated else 0

    # 3. RSI extreme — bonus (max 10)
    rsi_pts = 0.0
    best_rsi = None
    for sig in ta_signals:
        name = sig.get("name", "")
        if not name.startswith("RSI"):
            continue
        val = sig.get("value")
        if val is None:
            continue
        pts = 0.0
        if direction == "LONG":
            if val < 30:
                pts = 10
            elif val < 40:
                pts = 6
            elif val < 45:
                pts = 3
        elif direction == "SHORT":
            if val > 70:
                pts = 10
            elif val > 60:
                pts = 6
            elif val > 55:
                pts = 3
        if pts > rsi_pts:
            rsi_pts = pts
            best_rsi = val
    details["best_rsi"] = best_rsi

    # 4. Proximity to key level — bonus (max 10)
    # LONG → look for levels BELOW price (negative distance_pct = support)
    # SHORT → look for levels ABOVE price (positive distance_pct = resistance)
    level_pts = 0.0
    nearest_level = None
    for level in analysis.get("key_levels", []):
        try:
            raw_dist = Decimal(level.get("distance_pct", "99"))
        except (InvalidOperation, TypeError):
            continue
        # For LONG: we want levels below (raw_dist < 0), for SHORT: above (raw_dist > 0)
        if direction == "LONG" and raw_dist > Decimal("0"):
            continue
        if direction == "SHORT" and raw_dist < Decimal("0"):
            continue
        abs_dist = abs(raw_dist)
        if abs_dist >= Decimal("3"):
            continue
        pts = 0.0
        if abs_dist < Decimal("0.5"):
            pts = 10
        elif abs_dist < Decimal("1"):
            pts = 8
        elif abs_dist < Decimal("2"):
            pts = 5
        else:
            pts = 2
        if pts > level_pts:
            level_pts = pts
            nearest_level = level
    details["nearest_level"] = nearest_level

    # 5. Volume / orderbook / whale — combined bonus (max 10)
    extra_pts = 0.0
    has_buy = False
    has_sell = False
    for sig in ta_signals:
        name = sig.get("name", "")
        sc = sig.get("score", 0)
        if "B/S" in name or "OBV" in name:
            if direction == "LONG" and sc > 0.2:
                extra_pts += 2
                has_buy = True
            elif direction == "SHORT" and sc < -0.2:
                extra_pts += 2
                has_sell = True
    details["has_buy_pressure"] = has_buy
    details["has_sell_pressure"] = has_sell

    ob_confirming = False
    imbalance = orderbook_tracker.get_imbalance(symbol)
    if imbalance is not None:
        if (direction == "LONG" and imbalance > 0.1) or (direction == "SHORT" and imbalance < -0.1):
            extra_pts += 3
            ob_confirming = True
    details["ob_confirming"] = ob_confirming

    whale_confirming = False
    whales = whale_tracker.get_whale_alerts()
    for w in whales:
        ts = w.get("timestamp")
        if not ts:
            continue
        try:
            age = (now - datetime.fromisoformat(ts)).total_seconds()
        except (ValueError, TypeError):
            continue
        if age > 600 or w.get("symbol") != symbol:
            continue
        if (direction == "LONG" and w.get("side") == "BUY") or (direction == "SHORT" and w.get("side") == "SELL"):
            extra_pts += 3
            whale_confirming = True
            break
    details["whale_confirming"] = whale_confirming
    extra_pts = min(extra_pts, 10)

    # 6. Conflict penalty / alignment bonus
    conflict_pts = 0.0
    alerts = analysis.get("alerts", [])
    has_conflict = any(a.get("type") == "conflict" for a in alerts)
    has_aligned = any(a.get("type") == "aligned" for a in alerts)
    if has_conflict:
        conflict_pts = -15
    elif has_aligned:
        conflict_pts = 5

    total = bias_pts + agreement_pts + rsi_pts + level_pts + extra_pts + conflict_pts
    return max(0, min(total, 100)), details


# ── Message generation ────────────────────────────────────────

def _build_opportunity(analysis: dict, score: float, details: dict, now: datetime) -> dict:
    bias = analysis["bias"]
    symbol = analysis["symbol"]
    direction = bias["direction"]
    confidence = bias["confidence"]
    message = _build_message(symbol, direction, confidence, details)
    key_signals = _extract_key_signals(direction, confidence, details)
    ts = int(now.timestamp())

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
