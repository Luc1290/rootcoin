"""
Signal extraction per timeframe with structure detection.

Produces point-based scores (not averaged) for each component:
  - trend (MACD + MA): 0-15 pts
  - momentum (RSI + StochRSI + MFI): 0-15 pts
  - structure (rejection wicks, level tests, break-retest): 0-10 pts
"""

from decimal import Decimal

from backend.market import kline_manager

INDICATORS_SET = {"rsi", "macd", "ma", "bb", "mfi", "stoch_rsi", "buy_sell", "obv", "adx", "ema", "atr"}

STRUCTURE_LOOKBACK = {"5m": 24, "15m": 16}  # 5m: 2h, 15m: 4h
LEVEL_TOLERANCE = 0.003  # 0.3% proximity for level detection
WICK_RATIO = 2.0  # wick >= 2x body = rejection


# ── Public API ────────────────────────────────────────────────

async def extract_signals(
    symbol: str, interval: str, key_levels: list[dict], limit: int = 200,
) -> dict:
    await kline_manager.fetch_and_store(symbol, interval, limit=limit)
    klines = await kline_manager.get_klines(symbol, interval, limit=limit)
    if len(klines) < 30:
        return _empty_result()

    indicators = kline_manager.compute_indicators(klines, INDICATORS_SET)

    trend_pts, trend_dir, trend_signals = _score_trend(indicators, klines)
    mom_pts, mom_dir, mom_signals = _score_momentum(indicators, interval)

    structure_pts, structure_signals = 0.0, []
    if interval in STRUCTURE_LOOKBACK:
        level_prices = _extract_level_prices(key_levels)
        structure_pts, structure_signals = _score_structure(klines, level_prices, trend_dir, interval)

    bs_score = _get_buy_sell_score(indicators)
    raw_direction = trend_dir if trend_dir != 0 else mom_dir

    all_signals = []
    for s in trend_signals + mom_signals + structure_signals:
        s["name"] = f"{s['name']}({interval})" if "(" not in s["name"] else s["name"]
        all_signals.append(s)

    return {
        "trend": {"score": trend_pts, "signals": trend_signals},
        "momentum": {"score": mom_pts, "signals": mom_signals},
        "structure": {"score": structure_pts, "signals": structure_signals},
        "raw_direction": raw_direction,
        "adx": _last_valid(indicators.get("adx", [])),
        "atr": _last_valid(indicators.get("atr", [])),
        "bs_score": bs_score,
        "all_signals": all_signals,
    }


def _empty_result() -> dict:
    return {
        "trend": {"score": 0, "signals": []},
        "momentum": {"score": 0, "signals": []},
        "structure": {"score": 0, "signals": []},
        "raw_direction": 0,
        "adx": None,
        "atr": None,
        "bs_score": 0,
        "all_signals": [],
    }


# ── Trend scoring (0-15 pts) ─────────────────────────────────

def _score_trend(indicators: dict, klines: list[dict]) -> tuple[float, int, list[dict]]:
    pts = 0.0
    direction = 0
    signals = []
    n = len(klines) - 1
    close = float(klines[n]["close"])

    # MACD (0-8 pts)
    macd_hist = _last_valid(indicators.get("macd_hist", []))
    macd_hist_prev = _nth_valid(indicators.get("macd_hist", []), -2)
    if macd_hist is not None:
        if macd_hist > 0:
            if macd_hist_prev is not None and macd_hist > macd_hist_prev:
                macd_pts, macd_dir = 8.0, 1
            else:
                macd_pts, macd_dir = 4.0, 1
        elif macd_hist < 0:
            if macd_hist_prev is not None and macd_hist < macd_hist_prev:
                macd_pts, macd_dir = 8.0, -1
            else:
                macd_pts, macd_dir = 4.0, -1
            # Converging (becoming less negative)
            if macd_hist_prev is not None and macd_hist > macd_hist_prev:
                macd_pts, macd_dir = 3.0, -1
        else:
            macd_pts, macd_dir = 0.0, 0

        pts += macd_pts
        direction += macd_dir
        score_norm = macd_pts / 8.0 * macd_dir
        signals.append({
            "name": "MACD", "value": round(macd_hist, 4),
            "score": round(score_norm, 2), "weight": 1.0,
            "layer": "trend", "points": macd_pts,
        })

    # MA alignment (0-7 pts)
    ma7 = _last_valid(indicators.get("ma_7", []))
    ma25 = _last_valid(indicators.get("ma_25", []))
    if ma7 is not None and ma25 is not None:
        if close > ma25 and ma7 > ma25:
            ma_pts, ma_dir = 7.0, 1
        elif close > ma25:
            ma_pts, ma_dir = 4.0, 1
        elif close < ma25 and ma7 < ma25:
            ma_pts, ma_dir = 7.0, -1
        elif close < ma25:
            ma_pts, ma_dir = 4.0, -1
        else:
            ma_pts, ma_dir = 0.0, 0

        pts += ma_pts
        direction += ma_dir
        score_norm = ma_pts / 7.0 * ma_dir
        signals.append({
            "name": "MA", "value": round(ma7, 2),
            "score": round(score_norm, 2), "weight": 1.0,
            "layer": "trend", "points": ma_pts,
        })

    # Clamp direction to -1/0/+1
    direction = 1 if direction > 0 else (-1 if direction < 0 else 0)
    return min(pts, 15.0), direction, signals


# ── Momentum scoring (0-15 pts) ──────────────────────────────

def _score_momentum(indicators: dict, interval: str) -> tuple[float, int, list[dict]]:
    pts = 0.0
    direction = 0
    signals = []

    # RSI (0-6 pts)
    rsi = _last_valid(indicators.get("rsi", []))
    if rsi is not None:
        if rsi < 30:
            rsi_pts, rsi_dir = 6.0, 1
        elif rsi < 40:
            rsi_pts, rsi_dir = 4.0, 1
        elif rsi < 50:
            rsi_pts, rsi_dir = 2.0, 1
        elif rsi > 70:
            rsi_pts, rsi_dir = 6.0, -1
        elif rsi > 60:
            rsi_pts, rsi_dir = 4.0, -1
        elif rsi > 50:
            rsi_pts, rsi_dir = 2.0, -1
        else:
            rsi_pts, rsi_dir = 0.0, 0

        pts += rsi_pts
        direction += rsi_dir
        score_norm = rsi_pts / 6.0 * rsi_dir
        signals.append({
            "name": "RSI", "value": round(rsi, 1),
            "score": round(score_norm, 2), "weight": 1.0,
            "layer": "momentum", "points": rsi_pts,
        })

    # StochRSI (0-5 pts)
    stoch_k = _last_valid(indicators.get("stoch_rsi_k", []))
    stoch_d = _last_valid(indicators.get("stoch_rsi_d", []))
    if stoch_k is not None and stoch_d is not None:
        if stoch_k < 20 and stoch_k > stoch_d:
            sk_pts, sk_dir = 5.0, 1
        elif stoch_k < 30 and stoch_k > stoch_d:
            sk_pts, sk_dir = 3.0, 1
        elif stoch_k < 40:
            sk_pts, sk_dir = 1.0, 1
        elif stoch_k > 80 and stoch_k < stoch_d:
            sk_pts, sk_dir = 5.0, -1
        elif stoch_k > 70 and stoch_k < stoch_d:
            sk_pts, sk_dir = 3.0, -1
        elif stoch_k > 60:
            sk_pts, sk_dir = 1.0, -1
        else:
            sk_pts, sk_dir = 0.0, 0

        pts += sk_pts
        direction += sk_dir
        score_norm = sk_pts / 5.0 * sk_dir
        signals.append({
            "name": "StochRSI", "value": round(stoch_k, 1),
            "score": round(score_norm, 2), "weight": 1.0,
            "layer": "momentum", "points": sk_pts,
        })

    # MFI (0-4 pts)
    mfi = _last_valid(indicators.get("mfi", []))
    if mfi is not None:
        if mfi < 20:
            mfi_pts, mfi_dir = 4.0, 1
        elif mfi < 35:
            mfi_pts, mfi_dir = 2.0, 1
        elif mfi > 80:
            mfi_pts, mfi_dir = 4.0, -1
        elif mfi > 65:
            mfi_pts, mfi_dir = 2.0, -1
        else:
            mfi_pts, mfi_dir = 0.0, 0

        pts += mfi_pts
        direction += mfi_dir
        score_norm = mfi_pts / 4.0 * mfi_dir
        signals.append({
            "name": "MFI", "value": round(mfi, 1),
            "score": round(score_norm, 2), "weight": 1.0,
            "layer": "momentum", "points": mfi_pts,
        })

    direction = 1 if direction > 0 else (-1 if direction < 0 else 0)
    return min(pts, 15.0), direction, signals


# ── Structure detection (0-10 pts, 15m only) ─────────────────

def _score_structure(
    klines: list[dict], level_prices: list[float], direction: int,
    interval: str = "15m",
) -> tuple[float, list[dict]]:
    if not level_prices or direction == 0:
        return 0.0, []

    lookback = STRUCTURE_LOOKBACK.get(interval, 16)
    window = klines[-lookback:]
    if len(window) < 3:
        return 0.0, []

    pts = 0.0
    signals = []

    # 1. Rejection wick detection (0-4 pts)
    rej_pts, rej_signal = _detect_rejection_wicks(window, level_prices, direction)
    if rej_pts > 0:
        pts += rej_pts
        signals.append(rej_signal)

    # 2. Level test detection (0-3 pts)
    test_pts, test_signal = _detect_level_tests(window, level_prices, direction)
    if test_pts > 0:
        pts += test_pts
        signals.append(test_signal)

    # 3. Break-and-retest (0-3 pts)
    br_pts, br_signal = _detect_break_retest(window, level_prices, direction)
    if br_pts > 0:
        pts += br_pts
        signals.append(br_signal)

    return min(pts, 10.0), signals


def _detect_rejection_wicks(
    candles: list[dict], levels: list[float], direction: int,
) -> tuple[float, dict | None]:
    best_pts = 0.0
    best_ratio = 0.0

    for candle in candles:
        o, h, l, c = float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"])
        body = abs(c - o) or 0.0001  # avoid division by zero
        lower_wick = min(o, c) - l
        upper_wick = h - max(o, c)

        if direction == 1:  # LONG: look for bullish rejection (long lower wick near support)
            if lower_wick < WICK_RATIO * body:
                continue
            for lp in levels:
                if lp > min(o, c):  # level must be below body (it's a support)
                    continue
                if abs(l - lp) / lp <= LEVEL_TOLERANCE:
                    ratio = lower_wick / body
                    p = 4.0 if ratio >= 3.0 else 3.0
                    if p > best_pts:
                        best_pts = p
                        best_ratio = ratio
        else:  # SHORT: look for bearish rejection (long upper wick near resistance)
            if upper_wick < WICK_RATIO * body:
                continue
            for lp in levels:
                if lp < max(o, c):
                    continue
                if abs(h - lp) / lp <= LEVEL_TOLERANCE:
                    ratio = upper_wick / body
                    p = 4.0 if ratio >= 3.0 else 3.0
                    if p > best_pts:
                        best_pts = p
                        best_ratio = ratio

    if best_pts == 0:
        return 0.0, None

    label = "Rejection" if direction == 1 else "Rejection"
    score_norm = best_pts / 4.0 * direction
    return best_pts, {
        "name": "Rejection", "value": round(best_ratio, 1),
        "score": round(score_norm, 2), "weight": 1.0,
        "layer": "structure", "points": best_pts,
    }


def _detect_level_tests(
    candles: list[dict], levels: list[float], direction: int,
) -> tuple[float, dict | None]:
    best_count = 0
    best_level = None

    for lp in levels:
        count = 0
        for candle in candles:
            o, h, l, c = float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"])

            if direction == 1:  # LONG: test support (low near level, close above)
                if abs(l - lp) / lp <= LEVEL_TOLERANCE and c > lp * (1 + LEVEL_TOLERANCE * 0.66):
                    count += 1
            else:  # SHORT: test resistance (high near level, close below)
                if abs(h - lp) / lp <= LEVEL_TOLERANCE and c < lp * (1 - LEVEL_TOLERANCE * 0.66):
                    count += 1

        if count > best_count:
            best_count = count
            best_level = lp

    if best_count == 0:
        return 0.0, None

    pts = min(best_count, 3.0)
    score_norm = pts / 3.0 * direction
    return pts, {
        "name": "LevelTest", "value": best_count,
        "score": round(score_norm, 2), "weight": 1.0,
        "layer": "structure", "points": pts,
    }


def _detect_break_retest(
    candles: list[dict], levels: list[float], direction: int,
) -> tuple[float, dict | None]:
    for lp in levels:
        broke_below = False
        broke_above = False
        retested = False

        for candle in candles:
            c = float(candle["close"])

            if direction == 1:
                # LONG: price broke below support then came back above
                if c < lp * (1 - LEVEL_TOLERANCE):
                    broke_below = True
                elif broke_below and c > lp * (1 + LEVEL_TOLERANCE * 0.66):
                    retested = True
                    break
            else:
                # SHORT: price broke above resistance then came back below
                if c > lp * (1 + LEVEL_TOLERANCE):
                    broke_above = True
                elif broke_above and c < lp * (1 - LEVEL_TOLERANCE * 0.66):
                    retested = True
                    break

        if retested:
            score_norm = 1.0 * direction
            return 3.0, {
                "name": "Retest", "value": round(lp, 2),
                "score": round(score_norm, 2), "weight": 1.0,
                "layer": "structure", "points": 3.0,
            }

    return 0.0, None


# ── Buy/Sell pressure ─────────────────────────────────────────

def _get_buy_sell_score(indicators: dict) -> float:
    bs = _last_valid(indicators.get("buy_sell", []))
    if bs is None:
        return 0.0
    if bs > 5:
        return min(bs / 15, 1.0)
    elif bs < -5:
        return max(bs / 15, -1.0)
    return 0.0


# ── Level extraction ──────────────────────────────────────────

def _extract_level_prices(key_levels: list[dict]) -> list[float]:
    prices = []
    for level in key_levels:
        if level.get("type") == "current":
            continue
        try:
            prices.append(float(level["price"]))
        except (ValueError, KeyError, TypeError):
            continue
    return prices


# ── Helpers ───────────────────────────────────────────────────

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
