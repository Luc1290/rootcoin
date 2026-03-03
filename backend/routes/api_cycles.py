from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from backend.trading import position_tracker
from backend.core.database import async_session
from backend.core.models import Position
from backend.routes.position_helpers import format_duration

router = APIRouter(prefix="/api/cycles", tags=["cycles"])


def _cycle_to_dict(p: Position) -> dict:
    duration = None
    if p.opened_at:
        end = p.closed_at or datetime.now(timezone.utc)
        opened = p.opened_at if p.opened_at.tzinfo else p.opened_at.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        delta = end - opened
        duration = format_duration(int(delta.total_seconds()))

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
        q = select(
            func.count().label("total"),
            func.sum(case((Position.realized_pnl_pct > 0, 1), else_=0)).label("wins"),
            func.sum(func.coalesce(Position.realized_pnl, 0)).label("sum_pnl"),
            func.sum(func.coalesce(Position.entry_fees_usd, 0)).label("sum_entry_fees"),
            func.sum(func.coalesce(Position.exit_fees_usd, 0)).label("sum_exit_fees"),
        ).where(
            Position.is_active == False,
            Position.realized_pnl.isnot(None),
        )
        if symbol:
            q = q.where(Position.symbol == symbol)
        row = (await session.execute(q)).one()

    total = row.total or 0
    if total == 0:
        return {"total_cycles": 0, "wins": 0, "losses": 0, "win_rate": "0", "total_pnl": "0", "avg_pnl": "0"}

    wins = row.wins or 0
    total_net = (row.sum_pnl or Decimal("0")) - (row.sum_entry_fees or Decimal("0")) - (row.sum_exit_fees or Decimal("0"))

    return {
        "total_cycles": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": str(round(Decimal(wins) / Decimal(total) * 100, 1)),
        "total_pnl": str(round(total_net, 2)),
        "avg_pnl": str(round(total_net / total, 2)),
    }
