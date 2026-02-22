import asyncio
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog
from sqlalchemy import delete, select

from backend import binance_client
from backend.database import async_session
from backend.models import Kline

log = structlog.get_logger()

VALID_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")

INTERVAL_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400, "1d": 86400,
}

KLINE_RETENTION_DAYS = {
    "1m": 7, "5m": 30, "15m": 90,
    "1h": 365, "4h": 730, "1d": 1825,
}

CLEANUP_INTERVAL = 3600

_cleanup_task: asyncio.Task | None = None


async def start():
    global _cleanup_task
    _cleanup_task = asyncio.create_task(_run_cleanup())
    log.info("kline_manager_started")


async def stop():
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
    log.info("kline_manager_stopped")


# ── Fetch & store ──────────────────────────────────────────────

async def fetch_and_store(symbol: str, interval: str, limit: int = 500) -> int:
    client = await binance_client.get_client()

    start_time = await _get_last_open_time(symbol, interval)
    kwargs: dict = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time:
        kwargs["startTime"] = int(start_time.timestamp() * 1000) + 1

    raw_klines = await client.get_klines(**kwargs)
    if not raw_klines:
        return 0

    klines = [_parse_raw(symbol, interval, k) for k in raw_klines]
    await _upsert_klines(klines)
    return len(klines)


async def _get_last_open_time(symbol: str, interval: str) -> datetime | None:
    async with async_session() as session:
        result = await session.execute(
            select(Kline.open_time)
            .where(Kline.symbol == symbol, Kline.interval == interval)
            .order_by(Kline.open_time.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row


def _parse_raw(symbol: str, interval: str, raw: list) -> Kline:
    return Kline(
        symbol=symbol,
        interval=interval,
        open_time=datetime.fromtimestamp(raw[0] / 1000, tz=timezone.utc),
        open=Decimal(str(raw[1])),
        high=Decimal(str(raw[2])),
        low=Decimal(str(raw[3])),
        close=Decimal(str(raw[4])),
        volume=Decimal(str(raw[5])),
        close_time=datetime.fromtimestamp(raw[6] / 1000, tz=timezone.utc),
        quote_volume=Decimal(str(raw[7])),
        trade_count=int(raw[8]),
        taker_buy_base_vol=Decimal(str(raw[9])),
        taker_buy_quote_vol=Decimal(str(raw[10])),
    )


async def _upsert_klines(klines: list[Kline]):
    async with async_session() as session:
        for k in klines:
            existing = await session.execute(
                select(Kline).where(
                    Kline.symbol == k.symbol,
                    Kline.interval == k.interval,
                    Kline.open_time == k.open_time,
                )
            )
            row = existing.scalar_one_or_none()
            if row:
                row.open = k.open
                row.high = k.high
                row.low = k.low
                row.close = k.close
                row.volume = k.volume
                row.close_time = k.close_time
                row.quote_volume = k.quote_volume
                row.trade_count = k.trade_count
                row.taker_buy_base_vol = k.taker_buy_base_vol
                row.taker_buy_quote_vol = k.taker_buy_quote_vol
            else:
                session.add(k)
        await session.commit()


# ── Read from DB ───────────────────────────────────────────────

async def get_klines(symbol: str, interval: str, limit: int = 500) -> list[dict]:
    async with async_session() as session:
        # Subquery to get the N most recent, then re-order ASC for display
        sub = (
            select(Kline.id)
            .where(Kline.symbol == symbol, Kline.interval == interval)
            .order_by(Kline.open_time.desc())
            .limit(limit)
            .subquery()
        )
        result = await session.execute(
            select(Kline)
            .where(Kline.id.in_(select(sub.c.id)))
            .order_by(Kline.open_time.asc())
        )
        return [
            {
                "open_time": k.open_time.isoformat(),
                "open": str(k.open),
                "high": str(k.high),
                "low": str(k.low),
                "close": str(k.close),
                "volume": str(k.volume),
                "close_time": k.close_time.isoformat(),
                "quote_volume": str(k.quote_volume),
                "trade_count": k.trade_count,
                "taker_buy_vol": str(k.taker_buy_base_vol),
            }
            for k in result.scalars().all()
        ]


# ── Indicators ─────────────────────────────────────────────────

def compute_indicators(klines: list[dict], requested: set[str]) -> dict:
    closes = [float(k["close"]) for k in klines]
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]
    volumes = [float(k["volume"]) for k in klines]
    taker_buy = [float(k.get("taker_buy_vol", 0)) for k in klines]
    result: dict = {}

    if "ma" in requested:
        for period in (7, 25, 99):
            result[f"ma_{period}"] = _sma(closes, period)

    if "ema" in requested:
        for period in (7, 21, 50):
            result[f"ema_{period}"] = _ema(closes, period)

    if "rsi" in requested:
        result["rsi"] = _rsi(closes, 14)

    if "bb" in requested:
        mid, upper, lower = _bollinger(closes, 20, 2)
        result["bb_mid"] = mid
        result["bb_upper"] = upper
        result["bb_lower"] = lower

    if "obv" in requested:
        result["obv"] = _obv(closes, volumes)

    if "macd" in requested:
        macd_line, signal, histogram = _macd(closes)
        result["macd_line"] = macd_line
        result["macd_signal"] = signal
        result["macd_hist"] = histogram

    if "stoch_rsi" in requested:
        k_line, d_line = _stoch_rsi(closes, 14, 3, 3)
        result["stoch_rsi_k"] = k_line
        result["stoch_rsi_d"] = d_line

    if "atr" in requested:
        result["atr"] = _atr(highs, lows, closes, 14)

    if "vwap" in requested:
        result["vwap"] = _vwap(highs, lows, closes, volumes)

    if "adx" in requested:
        result["adx"] = _adx(highs, lows, closes, 14)

    if "mfi" in requested:
        result["mfi"] = _mfi(highs, lows, closes, volumes, 14)

    if "buy_sell" in requested:
        result["buy_sell"] = _buy_sell_pressure(volumes, taker_buy)

    return result


def _sma(data: list[float], period: int) -> list[float | None]:
    result: list[float | None] = []
    for i in range(len(data)):
        if i < period - 1:
            result.append(None)
        else:
            avg = sum(data[i - period + 1 : i + 1]) / period
            result.append(round(avg, 8))
    return result


def _rsi(data: list[float], period: int) -> list[float | None]:
    if len(data) < period + 1:
        return [None] * len(data)

    result: list[float | None] = [None] * period

    gains = []
    losses = []
    for i in range(1, period + 1):
        delta = data[i] - data[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(round(100 - (100 / (1 + rs)), 2))

    for i in range(period + 1, len(data)):
        delta = data[i] - data[i - 1]
        gain = max(delta, 0)
        loss = max(-delta, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(round(100 - (100 / (1 + rs)), 2))

    return result


def _bollinger(data: list[float], period: int, mult: float) -> tuple[list, list, list]:
    mid: list[float | None] = []
    upper: list[float | None] = []
    lower: list[float | None] = []

    for i in range(len(data)):
        if i < period - 1:
            mid.append(None)
            upper.append(None)
            lower.append(None)
        else:
            window = data[i - period + 1 : i + 1]
            avg = sum(window) / period
            variance = sum((x - avg) ** 2 for x in window) / period
            std = math.sqrt(variance)
            mid.append(round(avg, 8))
            upper.append(round(avg + mult * std, 8))
            lower.append(round(avg - mult * std, 8))

    return mid, upper, lower


def _obv(closes: list[float], volumes: list[float]) -> list[float | None]:
    if not closes:
        return []
    result: list[float | None] = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            result.append(round(result[-1] + volumes[i], 2))
        elif closes[i] < closes[i - 1]:
            result.append(round(result[-1] - volumes[i], 2))
        else:
            result.append(result[-1])
    return result


def _ema(data: list[float], period: int) -> list[float | None]:
    if len(data) < period:
        return [None] * len(data)
    result: list[float | None] = [None] * (period - 1)
    sma = sum(data[:period]) / period
    result.append(round(sma, 8))
    mult = 2 / (period + 1)
    for i in range(period, len(data)):
        val = (data[i] - result[-1]) * mult + result[-1]
        result.append(round(val, 8))
    return result


def _macd(closes: list[float], fast: int = 12, slow: int = 26, signal_p: int = 9):
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line: list[float | None] = []
    for f, s in zip(ema_fast, ema_slow):
        if f is not None and s is not None:
            macd_line.append(round(f - s, 8))
        else:
            macd_line.append(None)

    valid = [v for v in macd_line if v is not None]
    signal_raw = _ema(valid, signal_p) if len(valid) >= signal_p else [None] * len(valid)

    signal: list[float | None] = []
    histogram: list[float | None] = []
    vi = 0
    for m in macd_line:
        if m is None:
            signal.append(None)
            histogram.append(None)
        else:
            s = signal_raw[vi] if vi < len(signal_raw) else None
            signal.append(s)
            histogram.append(round(m - s, 8) if s is not None else None)
            vi += 1

    return macd_line, signal, histogram


def _stoch_rsi(closes: list[float], rsi_period: int = 14, k_period: int = 3, d_period: int = 3):
    rsi = _rsi(closes, rsi_period)
    rsi_vals = [v if v is not None else 0 for v in rsi]

    k_line: list[float | None] = []
    for i in range(len(rsi_vals)):
        if rsi[i] is None or i < rsi_period + k_period - 1:
            k_line.append(None)
        else:
            window = rsi_vals[i - k_period + 1 : i + 1]
            lo = min(window)
            hi = max(window)
            if hi == lo:
                k_line.append(50.0)
            else:
                k_line.append(round((rsi_vals[i] - lo) / (hi - lo) * 100, 2))

    k_valid = [v for v in k_line if v is not None]
    d_raw = _sma(k_valid, d_period) if len(k_valid) >= d_period else [None] * len(k_valid)

    d_line: list[float | None] = []
    vi = 0
    for k in k_line:
        if k is None:
            d_line.append(None)
        else:
            d_line.append(d_raw[vi] if vi < len(d_raw) else None)
            vi += 1

    return k_line, d_line


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14):
    if len(closes) < 2:
        return [None] * len(closes)
    tr: list[float] = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    result: list[float | None] = [None] * (period - 1)
    avg = sum(tr[:period]) / period
    result.append(round(avg, 8))
    for i in range(period, len(tr)):
        avg = (avg * (period - 1) + tr[i]) / period
        result.append(round(avg, 8))
    return result


def _vwap(highs: list[float], lows: list[float], closes: list[float], volumes: list[float]):
    result: list[float | None] = []
    cum_vol = 0.0
    cum_tp_vol = 0.0
    for i in range(len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        cum_vol += volumes[i]
        cum_tp_vol += tp * volumes[i]
        if cum_vol > 0:
            result.append(round(cum_tp_vol / cum_vol, 8))
        else:
            result.append(None)
    return result


def _adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14):
    if len(closes) < period + 1:
        return [None] * len(closes)

    plus_dm = []
    minus_dm = []
    tr_list = []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        tr_list.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    result: list[float | None] = [None]
    smooth_plus = sum(plus_dm[:period])
    smooth_minus = sum(minus_dm[:period])
    smooth_tr = sum(tr_list[:period])

    for _ in range(period - 1):
        result.append(None)

    dx_list = []
    for i in range(period - 1, len(plus_dm)):
        if i == period - 1:
            pass
        else:
            smooth_plus = smooth_plus - smooth_plus / period + plus_dm[i]
            smooth_minus = smooth_minus - smooth_minus / period + minus_dm[i]
            smooth_tr = smooth_tr - smooth_tr / period + tr_list[i]

        if smooth_tr == 0:
            result.append(None)
            dx_list.append(0)
            continue
        plus_di = 100 * smooth_plus / smooth_tr
        minus_di = 100 * smooth_minus / smooth_tr
        di_sum = plus_di + minus_di
        dx = 100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0
        dx_list.append(dx)

        if len(dx_list) < period:
            result.append(None)
        elif len(dx_list) == period:
            result.append(round(sum(dx_list) / period, 2))
        else:
            prev = result[-1] if result[-1] is not None else 0
            adx_val = (prev * (period - 1) + dx) / period
            result.append(round(adx_val, 2))

    return result


def _mfi(highs: list[float], lows: list[float], closes: list[float], volumes: list[float], period: int = 14):
    if len(closes) < period + 1:
        return [None] * len(closes)

    tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    raw_mf = [t * v for t, v in zip(tp, volumes)]

    result: list[float | None] = [None] * period
    for i in range(period, len(closes)):
        pos_flow = 0.0
        neg_flow = 0.0
        for j in range(i - period + 1, i + 1):
            if j > 0 and tp[j] > tp[j - 1]:
                pos_flow += raw_mf[j]
            elif j > 0:
                neg_flow += raw_mf[j]
        if neg_flow == 0:
            result.append(100.0)
        else:
            ratio = pos_flow / neg_flow
            result.append(round(100 - 100 / (1 + ratio), 2))

    return result


def _buy_sell_pressure(volumes: list[float], taker_buy: list[float]) -> list[float | None]:
    vol_sma_period = 20
    result: list[float | None] = []
    for i, (v, tb) in enumerate(zip(volumes, taker_buy)):
        if v > 0:
            raw = (tb / v) * 100 - 50
            # Weight by relative volume: dampen signal when volume is below average
            if i >= vol_sma_period:
                avg_vol = sum(volumes[i - vol_sma_period:i]) / vol_sma_period
                vol_weight = min(v / avg_vol, 1.5) if avg_vol > 0 else 1.0
            else:
                vol_weight = 1.0
            result.append(round(raw * vol_weight, 2))
        else:
            result.append(0)
    return result


# ── Cleanup ────────────────────────────────────────────────────

async def _run_cleanup():
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)
            async with async_session() as session:
                total_deleted = 0
                for interval, days in KLINE_RETENTION_DAYS.items():
                    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                    result = await session.execute(
                        delete(Kline).where(
                            Kline.interval == interval,
                            Kline.open_time < cutoff,
                        )
                    )
                    total_deleted += result.rowcount
                await session.commit()
            if total_deleted:
                log.info("klines_cleaned", deleted=total_deleted)
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("kline_cleanup_failed", exc_info=True)
