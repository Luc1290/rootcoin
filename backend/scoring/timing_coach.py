"""
Timing coach — evaluates whether NOW is the right moment to enter.

Produces a status (wait/ready/caution) with a checklist of conditions.
Used by market_analyzer to enrich analysis, and by opportunity_detector
to display timing badges on opportunity cards.
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from backend.market import orderbook_tracker

RETEST_THRESHOLD = Decimal("0.003")  # 0.3% distance = retest not reached
SPREAD_THRESHOLD = 0.05  # 0.05% spread = too wide for scalp

# Wall Street pre-open window (UTC): 14:25–14:35 (summer) / 15:25–15:35 (winter)
# Simplified: flag if between xx:25 and xx:40 UTC on weekdays
_WS_OPEN_HOURS_UTC = {14, 15}  # Could be either depending on DST


def evaluate(analysis: dict, symbol: str) -> dict:
    conditions = []

    direction = analysis.get("bias", {}).get("direction", "LONG")
    signals_5m = analysis.get("_signals_5m", {})
    key_levels = analysis.get("key_levels", [])
    current_price_str = analysis.get("current_price")

    try:
        current_price = Decimal(current_price_str) if current_price_str else None
    except (InvalidOperation, TypeError):
        current_price = None

    # 1. Level retest check
    if current_price and key_levels:
        _check_retest(conditions, direction, current_price, key_levels)

    # 2. MACD momentum check (5m)
    if signals_5m:
        _check_macd(conditions, direction, signals_5m)

    # 3. RSI overbought/oversold check (5m)
    if signals_5m:
        _check_rsi(conditions, direction, signals_5m)

    # 4. Spread check
    _check_spread(conditions, symbol)

    # 5. Wall Street session check
    _check_wall_street(conditions)

    # Determine overall status
    critical_unmet = [c for c in conditions if not c["met"] and c.get("critical", False)]
    caution_unmet = [c for c in conditions if not c["met"] and not c.get("critical", False)]

    if critical_unmet:
        status = "wait"
        summary = critical_unmet[0]["label"]
    elif caution_unmet:
        status = "caution"
        summary = caution_unmet[0]["label"]
    else:
        status = "ready"
        summary = "Conditions remplies"

    # Strip internal 'critical' flag from output
    clean_conditions = [{"label": c["label"], "met": c["met"]} for c in conditions]

    # Extract retest price + type from conditions if any
    retest_price = None
    retest_type = None
    for c in conditions:
        if c.get("_retest_price"):
            retest_price = str(c["_retest_price"])
            retest_type = c.get("_retest_type")
            break

    return {
        "status": status,
        "conditions": clean_conditions,
        "conditions_met": sum(1 for c in conditions if c["met"]),
        "conditions_total": len(conditions),
        "summary": summary,
        "retest_price": retest_price,
        "retest_type": retest_type,
    }


def _check_retest(conditions: list, direction: str, price: Decimal, key_levels: list):
    # Find nearest support (LONG) or resistance (SHORT)
    target = None
    best_dist = Decimal("99")

    for level in key_levels:
        if level.get("type") == "current":
            continue
        try:
            lp = Decimal(level["price"])
        except (InvalidOperation, KeyError, TypeError):
            continue

        dist = (lp - price) / price if price else Decimal("99")

        if direction == "LONG" and dist < 0:
            # Support below price
            abs_dist = abs(dist)
            if abs_dist < best_dist:
                best_dist = abs_dist
                target = level
        elif direction == "SHORT" and dist > 0:
            # Resistance above price
            abs_dist = abs(dist)
            if abs_dist < best_dist:
                best_dist = abs_dist
                target = level

    level_type = "plancher" if direction == "LONG" else "plafond"
    if target and best_dist > RETEST_THRESHOLD:
        label_price = target.get("label", target.get("price", ""))
        conditions.append({
            "label": f"Attendre {level_type} {label_price}",
            "met": False,
            "critical": True,
            "_retest_price": str(target.get("price", "")),
            "_retest_type": level_type,
        })
    elif target:
        conditions.append({
            "label": f"Retest {level_type} {target.get('label', '')}",
            "met": True,
            "critical": True,
            "_retest_price": str(target.get("price", "")),
            "_retest_type": level_type,
        })


def _check_macd(conditions: list, direction: str, signals_5m: dict):
    all_signals = signals_5m.get("all_signals", [])
    for sig in all_signals:
        if "MACD" not in sig.get("name", ""):
            continue
        score = sig.get("score", 0)
        if direction == "LONG" and score > 0:
            conditions.append({"label": "MACD 5min positif", "met": True, "critical": True})
        elif direction == "SHORT" and score < 0:
            conditions.append({"label": "MACD 5min negatif", "met": True, "critical": True})
        else:
            conditions.append({"label": "MACD 5min croise +", "met": False, "critical": True})
        return

    # No MACD signal found
    conditions.append({"label": "MACD 5min indisponible", "met": False, "critical": False})


def _check_rsi(conditions: list, direction: str, signals_5m: dict):
    all_signals = signals_5m.get("all_signals", [])
    for sig in all_signals:
        if "RSI" not in sig.get("name", "") or "Stoch" in sig.get("name", ""):
            continue
        rsi_val = sig.get("value")
        if rsi_val is None:
            continue
        if direction == "LONG" and rsi_val > 75:
            conditions.append({"label": f"RSI {rsi_val:.0f} — surachat", "met": False, "critical": False})
        elif direction == "SHORT" and rsi_val < 25:
            conditions.append({"label": f"RSI {rsi_val:.0f} — survente", "met": False, "critical": False})
        else:
            conditions.append({"label": f"RSI {rsi_val:.0f}", "met": True, "critical": False})
        return


def _check_spread(conditions: list, symbol: str):
    ob = orderbook_tracker.get_orderbook_data(symbol)
    raw = ob.get("spread_pct")
    try:
        spread_pct = float(raw) if raw is not None else None
    except (ValueError, TypeError):
        spread_pct = None
    if spread_pct is not None and spread_pct > SPREAD_THRESHOLD:
        conditions.append({
            "label": f"Spread {spread_pct:.3f}% — large",
            "met": False,
            "critical": False,
        })
    elif spread_pct is not None:
        conditions.append({
            "label": f"Spread {spread_pct:.3f}%",
            "met": True,
            "critical": False,
        })


def _check_wall_street(conditions: list):
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:  # Weekend
        return
    if now.hour in _WS_OPEN_HOURS_UTC and 25 <= now.minute <= 40:
        conditions.append({
            "label": "Ouverture Wall Street imminente",
            "met": False,
            "critical": False,
        })
