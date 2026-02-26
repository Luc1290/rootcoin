from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Query
from sqlalchemy import select

from backend.trading import position_tracker
from backend.core.database import async_session
from backend.core.models import Position

router = APIRouter(prefix="/api/cycles", tags=["cycles"])


def _cycle_to_dict(p: Position) -> dict:
    duration = None
    if p.opened_at:
        end = p.closed_at or datetime.now(timezone.utc)
        opened = p.opened_at if p.opened_at.tzinfo else p.opened_at.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        delta = end - opened
        total_secs = int(delta.total_seconds())
        hours, rem = divmod(total_secs, 3600)
        minutes = rem // 60
        if hours > 24:
            days = hours // 24
            duration = f"{days}d {hours % 24}h"
        else:
            duration = f"{hours}h {minutes}m"

    entry_fees = p.entry_fees_usd or Decimal("0")
    exit_fees = p.exit_fees_usd or Decimal("0")

    return {
        "id": p.id,
        "symbol": p.symbol,
        "side": p.side,
        "market_type": p.market_type,
        "entry_price": str(p.entry_price),
        "exit_price": str(p.exit_price) if p.exit_price else None,
        "quantity": str(p.entry_quantity or p.quantity),
        "entry_fees_usd": str(entry_fees),
        "exit_fees_usd": str(exit_fees),
        "total_fees_usd": str(entry_fees + exit_fees),
        "realized_pnl": str(p.realized_pnl) if p.realized_pnl is not None else None,
        "realized_pnl_pct": str(p.realized_pnl_pct) if p.realized_pnl_pct is not None else None,
        "current_price": str(p.current_price) if p.current_price else None,
        "pnl_usd": str(p.pnl_usd) if p.pnl_usd is not None else None,
        "pnl_pct": str(p.pnl_pct) if p.pnl_pct is not None else None,
        "is_active": p.is_active,
        "opened_at": p.opened_at.isoformat() if p.opened_at else None,
        "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        "duration": duration,
    }


@router.get("")
async def get_cycles(
    symbol: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    async with async_session() as session:
        q = select(Position).order_by(Position.opened_at.desc())
        if symbol:
            q = q.where(Position.symbol == symbol)
        if status == "open":
            q = q.where(Position.is_active == True)
        elif status == "closed":
            q = q.where(Position.is_active == False)
        # Exclude stale positions closed by scan (never had a real exit)
        from sqlalchemy import or_
        q = q.where(or_(
            Position.is_active == True,
            Position.closed_at.isnot(None),
            Position.realized_pnl.isnot(None),
        ))
        q = q.offset(offset).limit(limit)
        result = await session.execute(q)
        db_positions = result.scalars().all()

        # Merge live in-memory data for active positions
        live = {p.id: p for p in position_tracker.get_positions()}
        cycles = []
        for p in db_positions:
            if p.is_active and p.id in live:
                cycles.append(_cycle_to_dict(live[p.id]))
            else:
                cycles.append(_cycle_to_dict(p))
        return cycles


@router.get("/stats")
async def get_cycle_stats(symbol: str | None = Query(None)):
    async with async_session() as session:
        q = select(Position).where(
            Position.is_active == False,
            Position.realized_pnl.isnot(None),
        )
        if symbol:
            q = q.where(Position.symbol == symbol)
        result = await session.execute(q)
        closed = result.scalars().all()

        if not closed:
            return {"total_cycles": 0, "wins": 0, "losses": 0, "win_rate": "0", "total_pnl": "0", "avg_pnl": "0"}

        total_fees = sum((p.entry_fees_usd or Decimal("0")) + (p.exit_fees_usd or Decimal("0")) for p in closed)
        total_gross = sum(p.realized_pnl for p in closed if p.realized_pnl)
        total_net = total_gross - total_fees
        wins = [p for p in closed if p.realized_pnl_pct is not None and p.realized_pnl_pct > 0]
        losses = [p for p in closed if p.realized_pnl_pct is not None and p.realized_pnl_pct <= 0]

        return {
            "total_cycles": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": str(round(Decimal(len(wins)) / Decimal(len(closed)) * 100, 1)),
            "total_pnl": str(round(total_net, 2)),
            "avg_pnl": str(round(total_net / len(closed), 2)),
        }
