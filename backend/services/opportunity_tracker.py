"""
Opportunity lifecycle tracker — records detections, resolves outcomes.

Lifecycle: detected → taken/ignored → tp_hit/sl_hit/expired
"""

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

import structlog
from sqlalchemy import select, update

from backend.core.database import async_session
from backend.core.models import OpportunityRecord
from backend.trading import position_tracker

log = structlog.get_logger()

EXPIRY_HOURS = 4
RESOLVE_INTERVAL = 60  # seconds

_loop_task: asyncio.Task | None = None
_recent_cache: list[dict] = []


async def start():
    global _loop_task
    _loop_task = asyncio.create_task(_resolve_loop())
    log.info("opportunity_tracker_started")


async def stop():
    if _loop_task:
        _loop_task.cancel()
        try:
            await _loop_task
        except asyncio.CancelledError:
            pass
    log.info("opportunity_tracker_stopped")


async def record_detection(opp: dict):
    try:
        entry = Decimal(opp.get("levels", {}).get("entry", "0"))
        sl = Decimal(opp.get("levels", {}).get("sl", "0"))
        tp = Decimal(opp.get("levels", {}).get("tp1", "0"))
    except (InvalidOperation, TypeError):
        return

    if entry <= 0 or sl <= 0 or tp <= 0:
        return

    now = datetime.now(timezone.utc)
    record = OpportunityRecord(
        symbol=opp["symbol"],
        direction=opp["direction"],
        score=opp.get("score", 0),
        entry_price=entry,
        sl_price=sl,
        tp_price=tp,
        status="detected",
        detected_at=now,
    )
    async with async_session() as session:
        session.add(record)
        await session.commit()
    log.debug("opportunity_recorded", symbol=opp["symbol"], direction=opp["direction"])


async def mark_taken(symbol: str):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    async with async_session() as session:
        stmt = (
            update(OpportunityRecord)
            .where(
                OpportunityRecord.symbol == symbol,
                OpportunityRecord.status == "detected",
                OpportunityRecord.detected_at >= cutoff,
            )
            .values(status="taken")
        )
        result = await session.execute(stmt)
        await session.commit()
        if result.rowcount:
            _purge_active(symbol)
            log.info("opportunity_marked_taken", symbol=symbol, count=result.rowcount)


async def get_history(limit: int = 20) -> list[dict]:
    async with async_session() as session:
        stmt = (
            select(OpportunityRecord)
            .where(OpportunityRecord.status != "detected")
            .order_by(OpportunityRecord.detected_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [_to_dict(r) for r in rows]


async def get_stats() -> dict:
    async with async_session() as session:
        stmt = select(OpportunityRecord).where(
            OpportunityRecord.status.in_(["tp_hit", "sl_hit", "taken", "expired"])
        )
        rows = (await session.execute(stmt)).scalars().all()

    total = len(rows)
    if total == 0:
        return {"total": 0, "tp_hit": 0, "sl_hit": 0, "expired": 0, "taken": 0, "win_rate": 0, "avg_pnl_pct": 0}

    tp_hit = sum(1 for r in rows if r.status == "tp_hit")
    sl_hit = sum(1 for r in rows if r.status == "sl_hit")
    expired = sum(1 for r in rows if r.status == "expired")
    taken = sum(1 for r in rows if r.status == "taken")
    resolved = tp_hit + sl_hit
    win_rate = round(tp_hit / resolved * 100) if resolved else 0

    # Average PnL only on fully resolved records (exclude "taken" which are still open)
    pnls = [
        float(r.outcome_pnl_pct)
        for r in rows
        if r.outcome_pnl_pct is not None and r.status in ("tp_hit", "sl_hit", "expired")
    ]
    avg_pnl = round(sum(pnls) / len(pnls), 3) if pnls else 0

    return {
        "total": total,
        "tp_hit": tp_hit,
        "sl_hit": sl_hit,
        "expired": expired,
        "taken": taken,
        "win_rate": win_rate,
        "avg_pnl_pct": avg_pnl,
    }


def _purge_active(symbol: str):
    from backend.market import opportunity_detector
    opportunity_detector.remove_opportunity(symbol)


# ── Background resolve loop ──────────────────────────────────

async def _resolve_loop():
    await asyncio.sleep(30)
    while True:
        try:
            await _resolve_pending()
            await _check_taken_positions()
        except Exception:
            log.error("opportunity_resolve_failed", exc_info=True)
        await asyncio.sleep(RESOLVE_INTERVAL)


async def _resolve_pending():
    now = datetime.now(timezone.utc)
    expiry_cutoff = now.replace(tzinfo=None) - timedelta(hours=EXPIRY_HOURS)

    async with async_session() as session:
        stmt = select(OpportunityRecord).where(
            OpportunityRecord.status.in_(["detected", "taken"])
        )
        rows = (await session.execute(stmt)).scalars().all()

    for record in rows:
        current_price = _get_current_price(record.symbol)
        if current_price is None:
            continue

        outcome = _check_outcome(record, current_price)
        if outcome:
            await _update_record(record.id, outcome["status"], outcome.get("pnl_pct"))
            _purge_active(record.symbol)
            continue

        detected = record.detected_at.replace(tzinfo=None) if record.detected_at else now.replace(tzinfo=None)
        if detected < expiry_cutoff:
            pnl_pct = _calc_pnl_pct(record, current_price)
            await _update_record(record.id, "expired", pnl_pct)
            _purge_active(record.symbol)


async def _check_taken_positions():
    open_symbols = {
        p.symbol for p in position_tracker.get_positions() if p.is_active
    }
    if not open_symbols:
        return

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
    async with async_session() as session:
        stmt = select(OpportunityRecord).where(
            OpportunityRecord.status == "detected",
            OpportunityRecord.detected_at >= cutoff,
        )
        rows = (await session.execute(stmt)).scalars().all()

    for record in rows:
        if record.symbol in open_symbols:
            await mark_taken(record.symbol)


def _get_current_price(symbol: str) -> Decimal | None:
    from backend.market import market_analyzer

    analysis = market_analyzer.get_analysis(symbol)
    if analysis:
        price_str = analysis.get("current_price")
        if price_str:
            try:
                return Decimal(price_str)
            except (InvalidOperation, TypeError):
                pass

    for pos in position_tracker.get_positions():
        if pos.symbol == symbol and pos.current_price:
            return pos.current_price

    return None


def _check_outcome(record: OpportunityRecord, price: Decimal) -> dict | None:
    if record.direction == "LONG":
        if price >= record.tp_price:
            pnl = (record.tp_price - record.entry_price) / record.entry_price * 100
            return {"status": "tp_hit", "pnl_pct": pnl}
        if price <= record.sl_price:
            pnl = (record.sl_price - record.entry_price) / record.entry_price * 100
            return {"status": "sl_hit", "pnl_pct": pnl}
    else:
        if price <= record.tp_price:
            pnl = (record.entry_price - record.tp_price) / record.entry_price * 100
            return {"status": "tp_hit", "pnl_pct": pnl}
        if price >= record.sl_price:
            pnl = (record.entry_price - record.sl_price) / record.entry_price * 100
            return {"status": "sl_hit", "pnl_pct": pnl}
    return None


def _calc_pnl_pct(record: OpportunityRecord, price: Decimal) -> Decimal:
    if record.direction == "LONG":
        return (price - record.entry_price) / record.entry_price * 100
    return (record.entry_price - price) / record.entry_price * 100


async def _update_record(record_id: int, status: str, pnl_pct: Decimal | None = None):
    now = datetime.now(timezone.utc)
    values: dict = {"status": status, "resolved_at": now}
    if pnl_pct is not None:
        values["outcome_pnl_pct"] = pnl_pct

    async with async_session() as session:
        stmt = (
            update(OpportunityRecord)
            .where(OpportunityRecord.id == record_id)
            .values(**values)
        )
        await session.execute(stmt)
        await session.commit()
    log.info("opportunity_resolved", id=record_id, status=status, pnl_pct=str(pnl_pct))


def _to_dict(r: OpportunityRecord) -> dict:
    rr = None
    if r.entry_price and r.sl_price and r.tp_price:
        try:
            if r.direction == "LONG":
                risk = r.entry_price - r.sl_price
                reward = r.tp_price - r.entry_price
            else:
                risk = r.sl_price - r.entry_price
                reward = r.entry_price - r.tp_price
            if risk > 0:
                rr = str(round(reward / risk, 2))
        except Exception:
            pass

    return {
        "id": r.id,
        "symbol": r.symbol,
        "direction": r.direction,
        "score": r.score,
        "entry_price": str(r.entry_price),
        "sl_price": str(r.sl_price),
        "tp_price": str(r.tp_price),
        "rr": rr,
        "status": r.status,
        "outcome_pnl_pct": str(r.outcome_pnl_pct) if r.outcome_pnl_pct is not None else None,
        "detected_at": r.detected_at.isoformat() if r.detected_at else None,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
    }
