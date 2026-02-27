"""
Unified confluence-based scorer.

Combines signals from all timeframes into a single 0-100 score.
Layers:
  1. 15m primary:    0-40 pts (trend + momentum + structure)
  2. 1h confirmation: 0-25 pts (trend alignment + momentum confirmation)
  3. 4h context:     0-20 pts (trend support + key level bonus)
  4. Real-time flow: 0-15 pts (B/S pressure + orderbook + whales)
  5. Macro context:  -10 to +5 (penalty if opposing, small bonus if aligned)
"""

from datetime import datetime, timezone

import structlog

from backend.market import orderbook_tracker, whale_tracker, macro_tracker

log = structlog.get_logger()

LAYER_1_MAX = 40
LAYER_2_MAX = 25
LAYER_3_MAX = 20
LAYER_4_MAX = 15
MACRO_MIN = -10
MACRO_MAX = 5
TOTAL_MAX = LAYER_1_MAX + LAYER_2_MAX + LAYER_3_MAX + LAYER_4_MAX + MACRO_MAX  # 105


def compute_unified_score(
    signals_15m: dict,
    signals_1h: dict,
    signals_4h: dict,
    symbol: str,
    macro_data: dict,
    direction: int,
) -> dict:
    dir_str = "LONG" if direction >= 0 else "SHORT"

    l1 = _layer1_primary(signals_15m, direction)
    l2 = _layer2_confirmation(signals_1h, direction)
    l3 = _layer3_context(signals_4h, direction)
    l4 = _layer4_flow(signals_15m, symbol, direction)
    l5 = _layer5_macro(macro_data, direction)

    raw = l1 + l2 + l3 + l4 + l5
    score = _normalize(raw)

    # Breakdown for 15m primary layer
    t15 = signals_15m["trend"]["score"]
    m15 = signals_15m["momentum"]["score"]
    s15 = signals_15m["structure"]["score"]

    log.info(
        "scoring_result",
        symbol=symbol,
        direction=dir_str,
        score=score,
        raw=round(raw, 1),
        L1_15m=f"{round(l1, 1)}/{LAYER_1_MAX} (T{round(t15, 1)} M{round(m15, 1)} S{round(s15, 1)})",
        L2_1h=f"{round(l2, 1)}/{LAYER_2_MAX}",
        L3_4h=f"{round(l3, 1)}/{LAYER_3_MAX}",
        L4_flow=f"{round(l4, 1)}/{LAYER_4_MAX}",
        L5_macro=f"{round(l5, 1)}",
    )

    all_signals = (
        signals_15m.get("all_signals", [])
        + signals_1h.get("all_signals", [])
        + signals_4h.get("all_signals", [])
    )

    return {
        "direction": dir_str,
        "score": score,
        "raw_points": round(raw, 1),
        "layer_scores": {
            "primary_15m": round(l1, 1),
            "confirmation_1h": round(l2, 1),
            "context_4h": round(l3, 1),
            "flow": round(l4, 1),
            "macro": round(l5, 1),
        },
        "all_signals": all_signals,
    }


# ── Layer 1: 15m Primary (0-40) ──────────────────────────────

def _layer1_primary(signals_15m: dict, direction: int) -> float:
    trend_pts = signals_15m["trend"]["score"]
    momentum_pts = signals_15m["momentum"]["score"]
    structure_pts = signals_15m["structure"]["score"]

    # If 15m direction opposes the chosen direction, dampen
    raw_dir = signals_15m.get("raw_direction", 0)
    if raw_dir != 0 and raw_dir != direction:
        trend_pts *= 0.3
        momentum_pts *= 0.3

    return min(trend_pts + momentum_pts + structure_pts, LAYER_1_MAX)


# ── Layer 2: 1h Confirmation (0-25) ──────────────────────────

def _layer2_confirmation(signals_1h: dict, direction: int) -> float:
    raw_dir = signals_1h.get("raw_direction", 0)

    trend_pts = signals_1h["trend"]["score"]   # 0-15
    mom_pts = signals_1h["momentum"]["score"]  # 0-15

    if raw_dir == direction:
        # Aligned: full contribution (scaled to layer budget)
        trend_contrib = min(trend_pts, 15.0)
        mom_contrib = min(mom_pts * (10.0 / 15.0), 10.0)  # scale 0-15 → 0-10
    elif raw_dir == 0:
        # Neutral: partial credit
        trend_contrib = 5.0
        mom_contrib = 3.0
    else:
        # Opposing: no contribution
        trend_contrib = 0.0
        mom_contrib = 0.0

    return min(trend_contrib + mom_contrib, LAYER_2_MAX)


# ── Layer 3: 4h Context (0-20) ───────────────────────────────

def _layer3_context(signals_4h: dict, direction: int) -> float:
    raw_dir = signals_4h.get("raw_direction", 0)

    trend_pts = signals_4h["trend"]["score"]  # 0-15

    if raw_dir == direction:
        # Strong alignment
        trend_contrib = min(trend_pts, 15.0)
        level_bonus = 5.0  # 4h trend supports = structural strength
    elif raw_dir == 0:
        # Neutral: some credit
        trend_contrib = 5.0
        level_bonus = 2.0
    else:
        # Opposing: zero
        trend_contrib = 0.0
        level_bonus = 0.0

    return min(trend_contrib + level_bonus, LAYER_3_MAX)


# ── Layer 4: Real-time Flow (0-15) ───────────────────────────

def _layer4_flow(signals_15m: dict, symbol: str, direction: int) -> float:
    pts = 0.0

    # Buy/Sell pressure from 15m (0-5)
    bs = signals_15m.get("bs_score", 0)
    if direction == 1 and bs > 0:
        pts += min(bs * 5.0, 5.0)
    elif direction == -1 and bs < 0:
        pts += min(abs(bs) * 5.0, 5.0)

    # Orderbook imbalance (0-5)
    imbalance = orderbook_tracker.get_imbalance(symbol)
    if imbalance is not None:
        if direction == 1 and imbalance > 0.05:
            pts += min(imbalance / 0.3 * 5.0, 5.0)
        elif direction == -1 and imbalance < -0.05:
            pts += min(abs(imbalance) / 0.3 * 5.0, 5.0)

    # Whale activity (0-5)
    now = datetime.now(timezone.utc)
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
        side = w.get("side", "")
        if (direction == 1 and side == "BUY") or (direction == -1 and side == "SELL"):
            pts += 5.0
            break

    return min(pts, LAYER_4_MAX)


# ── Layer 5: Macro Context (-10 to +5) ───────────────────────

def _layer5_macro(macro_data: dict, direction: int) -> float:
    indicators = macro_data.get("indicators", {})
    if not indicators:
        return 0.0

    scores = []
    weights = []

    for name, cfg in _MACRO_CONFIGS.items():
        ind = indicators.get(name)
        if not ind:
            continue
        score = cfg["fn"](ind, direction)
        scores.append(score)
        weights.append(cfg["weight"])

    if not scores:
        return 0.0

    total_w = sum(weights)
    if total_w == 0:
        return 0.0
    weighted_avg = sum(s * w for s, w in zip(scores, weights)) / total_w

    # Map to -10..+5
    if weighted_avg < -0.4:
        return -10.0
    elif weighted_avg < -0.2:
        return -5.0
    elif weighted_avg > 0.4:
        return 5.0
    elif weighted_avg > 0.2:
        return 3.0
    return 0.0


def _macro_dxy(ind: dict, direction: int) -> float:
    trend = ind.get("trend", "")
    change = abs(float(ind.get("change_pct", 0)))
    magnitude = min(change / 1.0, 1.0)
    if trend == "up":
        return -magnitude  # DXY up = bearish crypto
    elif trend == "down":
        return magnitude
    return 0.0


def _macro_vix(ind: dict, direction: int) -> float:
    val = float(ind.get("value", 0))
    if val > 30:
        return -1.0
    elif val > 25:
        return -0.7
    elif val > 20:
        return -0.3
    elif val < 12:
        return 1.0
    elif val < 15:
        return 0.5
    return 0.0


def _macro_nasdaq(ind: dict, direction: int) -> float:
    trend = ind.get("trend", "")
    if trend == "up":
        return 0.5
    elif trend == "down":
        return -0.5
    return 0.0


def _macro_gold(ind: dict, direction: int) -> float:
    trend = ind.get("trend", "")
    if trend == "up":
        return -0.3
    elif trend == "down":
        return 0.2
    return 0.0


def _macro_us10y(ind: dict, direction: int) -> float:
    trend = ind.get("trend", "")
    change = abs(float(ind.get("change_pct", 0)))
    magnitude = min(change / 2.0, 1.0)
    if trend == "up":
        return -magnitude
    elif trend == "down":
        return magnitude
    return 0.0


def _macro_usdjpy(ind: dict, direction: int) -> float:
    trend = ind.get("trend", "")
    change = abs(float(ind.get("change_pct", 0)))
    magnitude = min(change / 1.0, 1.0)
    if trend == "down":
        return -magnitude * 0.7
    elif trend == "up":
        return magnitude * 0.3
    return 0.0


_MACRO_CONFIGS = {
    "dxy": {"fn": _macro_dxy, "weight": 1.0},
    "vix": {"fn": _macro_vix, "weight": 1.2},
    "nasdaq": {"fn": _macro_nasdaq, "weight": 0.8},
    "gold": {"fn": _macro_gold, "weight": 0.5},
    "us10y": {"fn": _macro_us10y, "weight": 0.8},
    "usdjpy": {"fn": _macro_usdjpy, "weight": 0.6},
}


# ── Normalization ─────────────────────────────────────────────

def _normalize(raw_points: float) -> int:
    clamped = max(0.0, min(raw_points, TOTAL_MAX))
    return round(clamped / TOTAL_MAX * 100)
