import asyncio
import time as _time
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import delete, func, select

from backend.core.database import async_session
from backend.core.models import NotificationLog

log = structlog.get_logger()

CLEANUP_INTERVAL = 3600  # 1 hour
RETENTION_DAYS = 30

# ── Confirmation filter (cross-type pending tracker) ─────────
PENDING_TTL = 600  # 10 min — window for cross-type confirmation

_pending: dict[str, float] = {}  # symbol → monotonic timestamp of 1st detection


def check_or_pend(symbol: str) -> bool:
    """Return True if symbol was already pending (= confirmed).
    Return False if this is the 1st detection (= now pending)."""
    now = _time.monotonic()
    expired = [s for s, t in _pending.items() if now - t > PENDING_TTL]
    for s in expired:
        del _pending[s]
    if symbol in _pending:
        del _pending[symbol]
        return True
    _pending[symbol] = now
    return False


def clear_pending(symbol: str):
    """Remove symbol from pending (after bypass notification)."""
    _pending.pop(symbol, None)

_cleanup_task: asyncio.Task | None = None


async def start():
    global _cleanup_task
    _cleanup_task = asyncio.create_task(_cleanup_loop())
    log.info("notification_logger_started")


async def stop():
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
    log.info("notification_logger_stopped")


async def record(
    notif_type: str,
    symbol: str,
    direction: str,
    change_pct: Decimal,
    window: str,
    price: Decimal,
    message: str,
    telegram_sent: bool,
    volume: Decimal | None = None,
    surge_ratio: Decimal | None = None,
) -> int | None:
    try:
        now = datetime.now(timezone.utc)
        row = NotificationLog(
            type=notif_type,
            symbol=symbol,
            direction=direction,
            change_pct=change_pct,
            window=window,
            price=price,
            volume=volume,
            surge_ratio=surge_ratio,
            message=message,
            telegram_sent=telegram_sent,
            created_at=now,
        )
        async with async_session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            row_id = row.id

        notif_dict = _to_dict(row)

        # Broadcast to WS clients
        from backend.routes import ws_dashboard
        asyncio.create_task(ws_dashboard.broadcast_notification(notif_dict))

        log.debug("notification_recorded", type=notif_type, symbol=symbol)
        return row_id
    except Exception:
        log.error("notification_record_failed", exc_info=True)
        return None


async def get_history(
    limit: int = 50,
    offset: int = 0,
    type_filter: str | None = None,
    symbol: str | None = None,
) -> list[dict]:
    async with async_session() as session:
        stmt = select(NotificationLog).order_by(NotificationLog.created_at.desc())
        if type_filter:
            stmt = stmt.where(NotificationLog.type == type_filter)
        if symbol:
            stmt = stmt.where(NotificationLog.symbol == symbol)
        stmt = stmt.offset(offset).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return [_to_dict(r) for r in rows]


async def get_stats() -> dict:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    async with async_session() as session:
        rows = (await session.execute(
            select(NotificationLog).where(NotificationLog.created_at >= month_ago)
        )).scalars().all()

    total_30d = len(rows)
    total_7d = sum(1 for r in rows if r.created_at and r.created_at >= week_ago)
    total_today = sum(1 for r in rows if r.created_at and r.created_at >= today_start)

    momentum = sum(1 for r in rows if r.type == "momentum")
    early_mover = sum(1 for r in rows if r.type == "early_mover")
    tg_sent = sum(1 for r in rows if r.telegram_sent)

    symbols = {}
    for r in rows:
        base = r.symbol.replace("USDC", "").replace("USDT", "")
        symbols[base] = symbols.get(base, 0) + 1
    top_symbols = sorted(symbols.items(), key=lambda x: -x[1])[:5]

    return {
        "today": total_today,
        "week": total_7d,
        "month": total_30d,
        "momentum": momentum,
        "early_mover": early_mover,
        "telegram_sent": tg_sent,
        "top_symbols": [{"symbol": s, "count": c} for s, c in top_symbols],
    }


# ── Cleanup ──────────────────────────────────────────────────

async def _cleanup_loop():
    await asyncio.sleep(60)
    while True:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
            async with async_session() as session:
                result = await session.execute(
                    delete(NotificationLog).where(NotificationLog.created_at < cutoff)
                )
                await session.commit()
                if result.rowcount:
                    log.info("notification_cleanup", deleted=result.rowcount)
        except Exception:
            log.error("notification_cleanup_failed", exc_info=True)
        await asyncio.sleep(CLEANUP_INTERVAL)


def _to_dict(r: NotificationLog) -> dict:
    return {
        "id": r.id,
        "type": r.type,
        "symbol": r.symbol,
        "direction": r.direction,
        "change_pct": str(r.change_pct),
        "window": r.window,
        "price": str(r.price),
        "volume": str(r.volume) if r.volume is not None else None,
        "surge_ratio": str(r.surge_ratio) if r.surge_ratio is not None else None,
        "message": r.message,
        "telegram_sent": r.telegram_sent,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
