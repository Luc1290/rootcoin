"""Pure level-computation helpers for trailing_manager.

All functions are stateless — no module globals, no I/O, no side effects.
Safe to call from any context without race-condition risk.
"""

from decimal import Decimal

# Defaults (shared with trailing_manager)
DEF_FALLBACK_SL_PCT = Decimal("1")   # fallback SL distance % when no key levels
DEF_MAX_SL_PCT = Decimal("1")        # max SL distance % from entry (cap losses)


def gain_pct(side: str, entry: Decimal, current: Decimal) -> Decimal:
    if side == "LONG":
        return (current - entry) / entry * 100
    return (entry - current) / entry * 100


def sl_protection_pct(side: str, entry: Decimal, auto_sl: Decimal) -> Decimal:
    if not auto_sl or entry <= 0:
        return Decimal(0)
    if side == "LONG":
        return (auto_sl - entry) / entry * 100
    return (entry - auto_sl) / entry * 100


def compute_rr(entry, sl, tp, side):
    if side == "LONG":
        risk = entry - sl
        reward = tp - entry
    else:
        risk = sl - entry
        reward = entry - tp
    if risk <= 0:
        return Decimal(0)
    return reward / risk


def parse_levels(key_levels: list) -> list[Decimal]:
    result = []
    for l in key_levels:
        try:
            result.append(Decimal(str(l["price"])))
        except (KeyError, ValueError, TypeError):
            continue
    result.sort()
    return result


def ensure_sl_tp(entry, side, sl, tp, min_rr):
    """Guarantee SL and TP are set, using percentage fallback if needed."""
    if not sl:
        pct = DEF_FALLBACK_SL_PCT / 100
        sl = entry * (1 - pct) if side == "LONG" else entry * (1 + pct)
    if not tp:
        risk = abs(entry - sl)
        tp = entry + risk * min_rr if side == "LONG" else entry - risk * min_rr
    return sl, tp


def find_initial_sl_tp(key_levels, entry_price, side):
    prices = parse_levels(key_levels)
    if not prices:
        return None, None

    min_dist = Decimal("0.6")
    max_dist = DEF_MAX_SL_PCT

    if side == "LONG":
        sl = None
        for p in reversed(prices):
            if p < entry_price:
                dist = (entry_price - p) / entry_price * 100
                if dist >= min_dist:
                    sl = p
                    break
        if sl:
            dist = (entry_price - sl) / entry_price * 100
            if dist > max_dist:
                sl = entry_price * (1 - max_dist / 100)
        tp = None
        for p in prices:
            if p > entry_price:
                dist = (p - entry_price) / entry_price * 100
                if dist >= min_dist:
                    tp = p
                    break
    else:
        sl = None
        for p in prices:
            if p > entry_price:
                dist = (p - entry_price) / entry_price * 100
                if dist >= min_dist:
                    sl = p
                    break
        if sl:
            dist = (sl - entry_price) / entry_price * 100
            if dist > max_dist:
                sl = entry_price * (1 + max_dist / 100)
        tp = None
        for p in reversed(prices):
            if p < entry_price:
                dist = (entry_price - p) / entry_price * 100
                if dist >= min_dist:
                    tp = p
                    break

    return sl, tp


def adjust_for_rr(key_levels, entry_price, side, min_rr):
    prices = parse_levels(key_levels)
    min_dist = Decimal("0.6")

    if side == "LONG":
        below = [p for p in prices
                 if p < entry_price and (entry_price - p) / entry_price * 100 >= min_dist]
        above = [p for p in prices
                 if p > entry_price and (p - entry_price) / entry_price * 100 >= min_dist]
        for sl in reversed(below):
            risk = entry_price - sl
            for tp in above:
                reward = tp - entry_price
                if risk > 0 and reward / risk >= min_rr:
                    return sl, tp
    else:
        above = [p for p in prices
                 if p > entry_price and (p - entry_price) / entry_price * 100 >= min_dist]
        below = [p for p in prices
                 if p < entry_price and (entry_price - p) / entry_price * 100 >= min_dist]
        for sl in above:
            risk = sl - entry_price
            for tp in reversed(below):
                reward = entry_price - tp
                if risk > 0 and reward / risk >= min_rr:
                    return sl, tp

    return None, None


def find_trailing_sl_level(key_levels, current_price, side, min_dist_pct=Decimal("0.6")):
    """Find nearest key level for trailing SL.

    SHORT: resistance ABOVE current_price (stop buy triggers there on bounce).
    LONG: support BELOW current_price (stop sell triggers there on pullback).
    """
    prices = parse_levels(key_levels)
    if not prices:
        return None

    if side == "SHORT":
        for p in prices:
            if p <= current_price:
                continue
            dist = (p - current_price) / current_price * 100
            if dist >= min_dist_pct:
                return p
    else:
        for p in reversed(prices):
            if p >= current_price:
                continue
            dist = (current_price - p) / current_price * 100
            if dist >= min_dist_pct:
                return p
    return None


def find_next_resistance(key_levels, current_price, side):
    prices = parse_levels(key_levels)
    min_dist = Decimal("0.5")

    if side == "LONG":
        for p in prices:
            if p > current_price:
                dist = (p - current_price) / current_price * 100
                if dist >= min_dist:
                    return p
    else:
        for p in reversed(prices):
            if p < current_price:
                dist = (current_price - p) / current_price * 100
                if dist >= min_dist:
                    return p
    return None


def compute_initial_levels(key_levels, entry_price, side, min_rr):
    """Compute initial SL/TP from key levels with R:R validation and fallback.

    Returns (sl_price, tp_price) — always non-None (fallback guarantees).
    """
    sl, tp = find_initial_sl_tp(key_levels, entry_price, side) if key_levels else (None, None)

    if sl and tp:
        rr = compute_rr(entry_price, sl, tp, side)
        if rr < min_rr:
            sl, tp = adjust_for_rr(key_levels, entry_price, side, min_rr)

    sl, tp = ensure_sl_tp(entry_price, side, sl, tp, min_rr)
    return sl, tp
