from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from backend.core.database import async_session
from backend.core.models import Order
from backend.trading.pnl import estimated_exit_fees


def format_duration(total_secs: int) -> str:
    hours, rem = divmod(total_secs, 3600)
    minutes = rem // 60
    if hours > 24:
        days = hours // 24
        return f"{days}d {hours % 24}h"
    return f"{hours}h {minutes}m"


async def fetch_order_prices(pos_ids: list[int]) -> dict:
    if not pos_ids:
        return {}
    async with async_session() as session:
        rows = (await session.execute(
            select(Order.position_id, Order.purpose, Order.stop_price, Order.price)
            .where(Order.position_id.in_(pos_ids), Order.status == "NEW",
                   Order.purpose.in_(["SL", "TP", "OCO"]))
        )).all()
    result = {}
    oco_pids = set()
    for pid, purpose, stop_price, price in rows:
        if purpose == "OCO":
            entry = result.setdefault(pid, {})
            entry["tp_price"] = str(price) if price else None
            entry["sl_price"] = str(stop_price) if stop_price else None
            oco_pids.add(pid)
    for pid, purpose, stop_price, price in rows:
        if purpose != "OCO" and pid not in oco_pids:
            entry = result.setdefault(pid, {})
            key = "sl_price" if purpose == "SL" else "tp_price"
            val = stop_price or price
            entry[key] = str(val) if val else None
    return result


def pos_to_dict(pos, order_prices=None) -> dict:
    duration = ""
    if pos.opened_at:
        opened = pos.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - opened
        duration = format_duration(int(delta.total_seconds()))

    current = pos.current_price or Decimal("0")
    qty = pos.quantity or Decimal("0")
    entry_fees = pos.entry_fees_usd or Decimal("0")
    exit_fees_est = estimated_exit_fees(qty, current)
    prices = (order_prices or {}).get(pos.id, {})

    return {
        "id": pos.id,
        "symbol": pos.symbol,
        "side": pos.side,
        "entry_price": str(pos.entry_price) if pos.entry_price else "0",
        "current_price": str(current),
        "quantity": str(qty),
        "pnl_usd": str(pos.pnl_usd) if pos.pnl_usd else "0",
        "pnl_pct": str(pos.pnl_pct) if pos.pnl_pct else "0",
        "entry_fees_usd": str(entry_fees),
        "exit_fees_est": str(exit_fees_est),
        "market_type": pos.market_type,
        "sl_order_id": pos.sl_order_id,
        "tp_order_id": pos.tp_order_id,
        "oco_order_list_id": pos.oco_order_list_id,
        "sl_price": prices.get("sl_price"),
        "tp_price": prices.get("tp_price"),
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        "duration": duration,
    }
