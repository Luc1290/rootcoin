import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select

from backend import kline_manager, macro_tracker, market_analyzer, orderbook_tracker, whale_tracker
from backend.database import async_session
from backend.models import Order, TradeSnapshot

log = structlog.get_logger()


async def capture_snapshot(
    position_id: int,
    snapshot_type: str,
    symbol: str,
    side: str,
    price: Decimal,
    quantity: Decimal,
    exit_reason: str | None = None,
):
    try:
        data = await _gather_context(symbol)

        if snapshot_type == "CLOSE" and not exit_reason:
            exit_reason = await _derive_exit_reason(position_id)

        snapshot = TradeSnapshot(
            position_id=position_id,
            snapshot_type=snapshot_type,
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            exit_reason=exit_reason,
            data=json.dumps(data, default=str),
            captured_at=datetime.now(timezone.utc),
        )
        async with async_session() as session:
            session.add(snapshot)
            await session.commit()
        log.info("snapshot_captured", position_id=position_id, type=snapshot_type, symbol=symbol)
    except Exception:
        log.warning("snapshot_capture_failed", position_id=position_id, exc_info=True)


async def _gather_context(symbol: str) -> dict:
    context = {}

    context["technical"] = await _get_technical(symbol)

    analysis = market_analyzer.get_analysis(symbol)
    if analysis:
        bias = analysis.get("bias", {})
        context["bias"] = {
            "direction": bias.get("direction"),
            "confidence": bias.get("confidence"),
            "ta_score": bias.get("ta_score"),
            "macro_score": bias.get("macro_score"),
        }
        context["key_levels"] = _extract_nearest_levels(
            analysis.get("key_levels", []),
            analysis.get("current_price"),
        )

    macro_data = macro_tracker.get_macro_data()
    indicators = macro_data.get("indicators", {})
    context["macro"] = {}
    for key in ("dxy", "vix", "nasdaq", "gold", "us10y"):
        ind = indicators.get(key)
        if ind:
            context["macro"][key] = {"value": ind.get("value"), "trend": ind.get("trend")}

    context["microstructure"] = _get_microstructure(symbol)

    return context


async def _get_technical(symbol: str) -> dict:
    try:
        klines_15m, klines_1h, klines_4h = await asyncio.gather(
            kline_manager.get_klines(symbol, "15m", limit=200),
            kline_manager.get_klines(symbol, "1h", limit=200),
            kline_manager.get_klines(symbol, "4h", limit=200),
        )
        result = {}
        for suffix, klines in [("15m", klines_15m), ("1h", klines_1h), ("4h", klines_4h)]:
            if len(klines) < 30:
                continue
            inds = kline_manager.compute_indicators(klines, {"rsi", "macd", "ma", "bb", "buy_sell", "adx"})
            for out_key, ind_key in [
                (f"rsi_{suffix}", "rsi"),
                (f"macd_hist_{suffix}", "macd_hist"),
                (f"ma7_{suffix}", "ma_7"),
                (f"ma25_{suffix}", "ma_25"),
                (f"buy_sell_{suffix}", "buy_sell"),
                (f"adx_{suffix}", "adx"),
            ]:
                if ind_key in inds:
                    result[out_key] = _last_valid(inds[ind_key])

            bb_upper = _last_valid(inds.get("bb_upper", []))
            bb_lower = _last_valid(inds.get("bb_lower", []))
            close = float(klines[-1]["close"])
            if bb_upper and bb_lower and (bb_upper - bb_lower) > 0:
                result[f"bb_position_{suffix}"] = round((close - bb_lower) / (bb_upper - bb_lower), 3)
        return result
    except Exception:
        log.warning("snapshot_technical_failed", symbol=symbol, exc_info=True)
        return {}


def _get_microstructure(symbol: str) -> dict:
    result = {}
    imbalance = orderbook_tracker.get_imbalance(symbol)
    if imbalance is not None:
        result["orderbook_imbalance"] = round(imbalance, 3)

    whales = whale_tracker.get_whale_alerts()
    symbol_whales = [w for w in whales if w.get("symbol") == symbol]
    result["whale_recent_count"] = len(symbol_whales)
    if symbol_whales:
        buys = sum(1 for w in symbol_whales if w.get("side") == "BUY")
        result["whale_recent_bias"] = "BUY" if buys > len(symbol_whales) / 2 else "SELL"
    return result


def _extract_nearest_levels(key_levels: list, current_price_str: str | None) -> dict:
    if not key_levels or not current_price_str:
        return {}
    try:
        current = float(current_price_str)
    except (ValueError, TypeError):
        return {}
    nearest_support = None
    nearest_resistance = None
    for lvl in key_levels:
        try:
            price = float(lvl.get("price", 0))
        except (ValueError, TypeError):
            continue
        if price >= current and (nearest_resistance is None or price < nearest_resistance):
            nearest_resistance = price
        if price < current and (nearest_support is None or price > nearest_support):
            nearest_support = price
    result = {}
    if nearest_support:
        result["nearest_support"] = str(round(nearest_support, 2))
        result["distance_to_support_pct"] = str(round((nearest_support - current) / current * 100, 2))
    if nearest_resistance:
        result["nearest_resistance"] = str(round(nearest_resistance, 2))
        result["distance_to_resistance_pct"] = str(round((nearest_resistance - current) / current * 100, 2))
    return result


async def _derive_exit_reason(position_id: int) -> str:
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Order.purpose).where(
                    Order.position_id == position_id,
                    Order.status == "FILLED",
                ).order_by(Order.updated_at.desc()).limit(1)
            )
            purpose = result.scalar_one_or_none()
            if purpose in ("SL", "TP", "OCO", "CLOSE"):
                return purpose
    except Exception:
        log.warning("exit_reason_lookup_failed", position_id=position_id, exc_info=True)
    return "MANUAL"


def _last_valid(data: list) -> float | None:
    for v in reversed(data):
        if v is not None:
            return round(v, 4)
    return None
