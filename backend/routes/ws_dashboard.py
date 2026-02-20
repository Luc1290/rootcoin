import asyncio
import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend import position_tracker, ws_manager
from backend.ws_manager import (
    EVENT_ACCOUNT_UPDATE,
    EVENT_EXECUTION_REPORT,
    EVENT_PRICE_UPDATE,
)

log = structlog.get_logger()

router = APIRouter()

_clients: set[WebSocket] = set()

POSITION_BROADCAST_INTERVAL = 2


async def _broadcast(message: dict):
    if not _clients:
        return
    raw = json.dumps(message)
    dead = set()
    for ws in _clients:
        try:
            await ws.send_text(raw)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


async def _on_price_update(msg: dict):
    await _broadcast({
        "type": "price_update",
        "data": {
            "symbol": msg.get("s", ""),
            "price": msg.get("c", "0"),
            "change_24h": msg.get("P", "0"),
        },
    })


async def _on_execution_report(msg: dict):
    await _broadcast({
        "type": "order_update",
        "data": {
            "order_id": str(msg.get("i", "")),
            "symbol": msg.get("s", ""),
            "status": msg.get("X", ""),
            "type": msg.get("o", ""),
            "side": msg.get("S", ""),
            "price": msg.get("L", "0"),
            "filled_qty": msg.get("l", "0"),
        },
    })


async def _on_account_update(msg: dict):
    balances = msg.get("B", [])
    await _broadcast({
        "type": "balance_update",
        "data": [
            {"asset": b.get("a", ""), "free": b.get("f", "0"), "locked": b.get("l", "0")}
            for b in balances
        ],
    })


def _pos_to_ws(pos) -> dict:
    duration = ""
    if pos.opened_at:
        from datetime import datetime, timezone
        opened = pos.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - opened
        hours, rem = divmod(int(delta.total_seconds()), 3600)
        minutes = rem // 60
        if hours > 24:
            days = hours // 24
            duration = f"{days}d {hours % 24}h"
        else:
            duration = f"{hours}h {minutes}m"

    from decimal import Decimal
    entry_fees = pos.entry_fees_usd or Decimal("0")
    current = pos.current_price or Decimal("0")
    qty = pos.quantity or Decimal("0")
    exit_fees_est = qty * current * Decimal("0.001")

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
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        "duration": duration,
    }


async def _broadcast_positions():
    while True:
        try:
            await asyncio.sleep(POSITION_BROADCAST_INTERVAL)
            if not _clients:
                continue
            positions = position_tracker.get_positions()
            await _broadcast({
                "type": "positions_snapshot",
                "data": [_pos_to_ws(p) for p in positions],
            })
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("position_broadcast_failed", exc_info=True)


_broadcast_task: asyncio.Task | None = None


def _ensure_callbacks():
    global _broadcast_task
    if _broadcast_task is None:
        ws_manager.on(EVENT_PRICE_UPDATE, _on_price_update)
        ws_manager.on(EVENT_EXECUTION_REPORT, _on_execution_report)
        ws_manager.on(EVENT_ACCOUNT_UPDATE, _on_account_update)
        _broadcast_task = asyncio.create_task(_broadcast_positions())


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    _ensure_callbacks()
    log.info("frontend_ws_connected", clients=len(_clients))

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)
        log.info("frontend_ws_disconnected", clients=len(_clients))
