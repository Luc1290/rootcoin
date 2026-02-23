import asyncio
import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend import market_analyzer, news_tracker, position_tracker, ws_manager
from backend.routes.position_helpers import fetch_order_prices, pos_to_dict
from backend.ws_manager import (
    EVENT_ACCOUNT_UPDATE,
    EVENT_EXECUTION_REPORT,
    EVENT_KLINE_UPDATE,
    EVENT_PRICE_UPDATE,
)

log = structlog.get_logger()

router = APIRouter()

_clients: set[WebSocket] = set()
_positions_dirty = True  # start dirty for initial broadcast

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
    global _positions_dirty
    if not _positions_dirty and position_tracker.get_positions():
        _positions_dirty = True
    await _broadcast({
        "type": "price_update",
        "data": {
            "symbol": msg.get("s", ""),
            "price": msg.get("c", "0"),
            "change_24h": msg.get("P", "0"),
        },
    })


async def _on_execution_report(msg: dict):
    global _positions_dirty
    _positions_dirty = True
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


async def _on_kline_update(msg: dict):
    await _broadcast({"type": "kline_update", "data": msg})


async def _on_account_update(msg: dict):
    balances = msg.get("B", [])
    await _broadcast({
        "type": "balance_update",
        "data": [
            {"asset": b.get("a", ""), "free": b.get("f", "0"), "locked": b.get("l", "0")}
            for b in balances
        ],
    })


async def _broadcast_positions():
    global _positions_dirty
    while True:
        try:
            await asyncio.sleep(POSITION_BROADCAST_INTERVAL)
            if not _clients or not _positions_dirty:
                continue
            _positions_dirty = False
            positions = position_tracker.get_positions()
            pos_ids = [p.id for p in positions if p.sl_order_id or p.tp_order_id or p.oco_order_list_id]
            order_prices = await fetch_order_prices(pos_ids)
            await _broadcast({
                "type": "positions_snapshot",
                "data": [pos_to_dict(p, order_prices) for p in positions],
            })
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("position_broadcast_failed", exc_info=True)


ANALYSIS_BROADCAST_INTERVAL = 30

_broadcast_task: asyncio.Task | None = None
_analysis_task: asyncio.Task | None = None
_news_task: asyncio.Task | None = None


async def _broadcast_analysis():
    while True:
        try:
            await asyncio.sleep(ANALYSIS_BROADCAST_INTERVAL)
            if not _clients:
                continue
            data = market_analyzer.get_all_analyses()
            if data:
                await _broadcast({"type": "analysis_update", "data": data})
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("analysis_broadcast_failed", exc_info=True)


NEWS_BROADCAST_INTERVAL = 120


async def _broadcast_news():
    while True:
        try:
            await asyncio.sleep(NEWS_BROADCAST_INTERVAL)
            if not _clients:
                continue
            data = news_tracker.get_news()
            if data and data.get("items"):
                await _broadcast({"type": "news_update", "data": data})
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("news_broadcast_failed", exc_info=True)


def _ensure_callbacks():
    global _broadcast_task, _analysis_task, _news_task
    if _broadcast_task is None:
        ws_manager.on(EVENT_PRICE_UPDATE, _on_price_update)
        ws_manager.on(EVENT_EXECUTION_REPORT, _on_execution_report)
        ws_manager.on(EVENT_ACCOUNT_UPDATE, _on_account_update)
        ws_manager.on(EVENT_KLINE_UPDATE, _on_kline_update)
        _broadcast_task = asyncio.create_task(_broadcast_positions())
        _analysis_task = asyncio.create_task(_broadcast_analysis())
        _news_task = asyncio.create_task(_broadcast_news())


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global _positions_dirty
    await ws.accept()
    _clients.add(ws)
    _positions_dirty = True
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
