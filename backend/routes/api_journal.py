import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from backend.database import async_session
from backend.models import Balance, Position, TradeSnapshot

router = APIRouter(prefix="/api/journal", tags=["journal"])


# ── Calendar PnL heatmap ─────────────────────────────────────


@router.get("/calendar")
async def get_calendar_data(year: int | None = Query(None)):
    now = datetime.now(timezone.utc)
    target_year = year or now.year
    start_date = datetime(target_year, 1, 1, tzinfo=timezone.utc)
    end_date = datetime(target_year + 1, 1, 1, tzinfo=timezone.utc)

    async with async_session() as session:
        result = await session.execute(
            select(Position).where(
                Position.is_active == False,
                Position.closed_at.isnot(None),
                Position.closed_at >= start_date,
                Position.closed_at < end_date,
                Position.realized_pnl.isnot(None),
            )
        )
        positions = result.scalars().all()

    daily: dict[str, dict] = {}
    for p in positions:
        closed = p.closed_at
        if closed.tzinfo is None:
            closed = closed.replace(tzinfo=timezone.utc)
        day_key = closed.strftime("%Y-%m-%d")
        if day_key not in daily:
            daily[day_key] = {"pnl": Decimal("0"), "trades": 0, "wins": 0}
        fees = (p.entry_fees_usd or Decimal("0")) + (p.exit_fees_usd or Decimal("0"))
        net_pnl = p.realized_pnl - fees
        daily[day_key]["pnl"] += net_pnl
        daily[day_key]["trades"] += 1
        if p.realized_pnl_pct and p.realized_pnl_pct > 0:
            daily[day_key]["wins"] += 1

    return {
        "year": target_year,
        "days": [
            {
                "date": day,
                "pnl": str(round(data["pnl"], 2)),
                "trades": data["trades"],
                "wins": data["wins"],
            }
            for day, data in sorted(daily.items())
        ],
    }


# ── Equity curve + drawdown ──────────────────────────────────


@router.get("/equity")
async def get_equity_curve(
    hours: int = Query(720, le=8760),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with async_session() as session:
        result = await session.execute(
            select(
                Balance.snapshot_at,
                func.sum(Balance.usd_value).label("total_usd"),
            )
            .where(Balance.snapshot_at >= cutoff, Balance.usd_value.isnot(None))
            .group_by(Balance.snapshot_at)
            .order_by(Balance.snapshot_at.asc())
        )
        rows = result.all()

    if not rows:
        return {"points": [], "max_drawdown_pct": "0", "current_drawdown_pct": "0"}

    points = []
    peak = Decimal("0")
    max_dd_pct = Decimal("0")

    for row in rows:
        total = row.total_usd or Decimal("0")
        if total > peak:
            peak = total
        dd_pct = ((peak - total) / peak * 100) if peak > 0 else Decimal("0")
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

        points.append({
            "snapshot_at": row.snapshot_at.isoformat(),
            "total_usd": str(round(total, 2)),
            "drawdown_pct": str(round(dd_pct, 2)),
        })

    current_dd = points[-1]["drawdown_pct"] if points else "0"

    return {
        "points": points,
        "max_drawdown_pct": str(round(max_dd_pct, 2)),
        "current_drawdown_pct": current_dd,
    }


# ── Journal entries (positions + snapshots) ──────────────────


@router.get("/entries")
async def get_journal_entries(
    symbol: str | None = Query(None),
    limit: int = Query(30, le=100),
    offset: int = Query(0),
):
    async with async_session() as session:
        q = (
            select(Position)
            .where(
                Position.is_active == False,
                Position.closed_at.isnot(None),
                Position.realized_pnl.isnot(None),
            )
            .order_by(Position.closed_at.desc())
        )
        if symbol:
            q = q.where(Position.symbol == symbol)
        q = q.offset(offset).limit(limit)
        result = await session.execute(q)
        positions = result.scalars().all()

        if not positions:
            return []

        pos_ids = [p.id for p in positions]
        snap_result = await session.execute(
            select(TradeSnapshot)
            .where(TradeSnapshot.position_id.in_(pos_ids))
            .order_by(TradeSnapshot.captured_at.asc())
        )
        all_snaps = snap_result.scalars().all()

    snaps_by_pos: dict[int, list] = {}
    for s in all_snaps:
        snaps_by_pos.setdefault(s.position_id, []).append(s)

    entries = []
    for p in positions:
        entry_fees = p.entry_fees_usd or Decimal("0")
        exit_fees = p.exit_fees_usd or Decimal("0")
        total_fees = entry_fees + exit_fees

        duration = None
        if p.opened_at and p.closed_at:
            opened = p.opened_at if p.opened_at.tzinfo else p.opened_at.replace(tzinfo=timezone.utc)
            closed = p.closed_at if p.closed_at.tzinfo else p.closed_at.replace(tzinfo=timezone.utc)
            delta = closed - opened
            total_secs = int(delta.total_seconds())
            hours_val, rem = divmod(total_secs, 3600)
            minutes = rem // 60
            if hours_val > 24:
                days = hours_val // 24
                duration = f"{days}d {hours_val % 24}h"
            else:
                duration = f"{hours_val}h {minutes}m"

        snapshots = snaps_by_pos.get(p.id, [])
        open_snap = next((s for s in snapshots if s.snapshot_type == "OPEN"), None)
        close_snap = next((s for s in snapshots if s.snapshot_type == "CLOSE"), None)

        entries.append({
            "id": p.id,
            "symbol": p.symbol,
            "side": p.side,
            "market_type": p.market_type,
            "entry_price": str(p.entry_price),
            "exit_price": str(p.exit_price) if p.exit_price else None,
            "quantity": str(p.entry_quantity or p.quantity),
            "total_fees_usd": str(round(total_fees, 4)),
            "realized_pnl": str(p.realized_pnl) if p.realized_pnl is not None else None,
            "realized_pnl_pct": str(p.realized_pnl_pct) if p.realized_pnl_pct is not None else None,
            "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            "closed_at": p.closed_at.isoformat() if p.closed_at else None,
            "duration": duration,
            "open_snapshot": _snap_to_dict(open_snap) if open_snap else None,
            "close_snapshot": _snap_to_dict(close_snap) if close_snap else None,
        })

    return entries


def _snap_to_dict(s: TradeSnapshot) -> dict:
    return {
        "snapshot_type": s.snapshot_type,
        "price": str(s.price),
        "quantity": str(s.quantity),
        "exit_reason": s.exit_reason,
        "data": json.loads(s.data) if s.data else {},
        "captured_at": s.captured_at.isoformat(),
    }
