import asyncio
import os
import sys
import time
from datetime import datetime, timezone

import structlog
from sqlalchemy import text

from backend import (
    balance_tracker,
    event_recorder,
    heatmap_manager,
    kline_manager,
    macro_tracker,
    market_analyzer,
    news_tracker,
    orderbook_tracker,
    position_tracker,
    price_recorder,
    whale_tracker,
    ws_manager,
)
from backend.database import DB_PATH, async_session

log = structlog.get_logger()

COLLECT_INTERVAL = 10

_started_at: float | None = None
_collect_task: asyncio.Task | None = None
_cached_health: dict = {}


async def start():
    global _started_at, _collect_task
    _started_at = time.monotonic()
    _collect_task = asyncio.create_task(_run_collect())
    log.info("health_collector_started")


async def stop():
    if _collect_task:
        _collect_task.cancel()
        try:
            await _collect_task
        except asyncio.CancelledError:
            pass
    log.info("health_collector_stopped")


def get_health() -> dict:
    return _cached_health


async def _run_collect():
    while True:
        try:
            _cached_health.update(await _collect())
        except asyncio.CancelledError:
            break
        except Exception:
            log.error("health_collect_failed", exc_info=True)
        await asyncio.sleep(COLLECT_INTERVAL)


async def _collect() -> dict:
    now_utc = datetime.now(timezone.utc)
    uptime_s = time.monotonic() - _started_at if _started_at else 0
    return {
        "collected_at": now_utc.isoformat(),
        "uptime_s": round(uptime_s),
        "websockets": ws_manager.get_ws_health(),
        "modules": _collect_module_status(),
        "database": await _collect_db_stats(),
        "memory": _collect_memory_stats(),
        "process": _collect_process_stats(),
    }


_MODULE_REGISTRY = [
    ("ws_manager", ws_manager, "_tasks", True),
    ("position_tracker", position_tracker, "_reconcile_task", False),
    ("price_recorder", price_recorder, "_cleanup_task", False),
    ("balance_tracker", balance_tracker, "_snapshot_task", False),
    ("kline_manager", kline_manager, "_cleanup_task", False),
    ("macro_tracker", macro_tracker, "_refresh_task", False),
    ("whale_tracker", whale_tracker, "_stream_task", False),
    ("orderbook_tracker", orderbook_tracker, "_poll_task", False),
    ("heatmap_manager", heatmap_manager, "_refresh_task", False),
    ("market_analyzer", market_analyzer, "_refresh_task", False),
    ("news_tracker", news_tracker, "_refresh_task", False),
]


def _collect_module_status() -> list[dict]:
    modules = []
    for name, mod, task_attr, is_list in _MODULE_REGISTRY:
        task = getattr(mod, task_attr, None)
        if is_list and isinstance(task, list):
            alive = all(t and not t.done() for t in task) if task else False
        elif task:
            alive = not task.done()
        else:
            alive = False

        is_stale = _check_staleness(name, mod)

        status = "healthy"
        if not alive:
            status = "unhealthy"
        elif is_stale:
            status = "degraded"

        modules.append({
            "name": name,
            "alive": alive,
            "is_stale": is_stale,
            "status": status,
        })
    return modules


def _check_staleness(name: str, mod) -> bool | None:
    if name == "macro_tracker":
        data = mod.get_macro_data()
        return data.get("is_stale") if data else None
    if name == "heatmap_manager":
        data = mod.get_heatmap_data()
        return data.get("is_stale") if data else None
    if name == "news_tracker":
        data = mod.get_news()
        return data.get("is_stale") if data else None
    if name == "position_tracker":
        return not mod.is_reconciled()
    return None


async def _collect_db_stats() -> dict:
    db_size_bytes = 0
    if DB_PATH.exists():
        try:
            db_size_bytes = DB_PATH.stat().st_size
        except OSError:
            pass

    table_counts = {}
    tables = ["positions", "trades", "orders", "balances", "prices", "klines"]
    t0 = time.monotonic()
    try:
        async with async_session() as session:
            for table in tables:
                result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                table_counts[table] = result.scalar()
    except Exception:
        log.error("health_db_stats_failed", exc_info=True)
    query_latency_ms = round((time.monotonic() - t0) * 1000, 1)

    return {
        "file_size_mb": round(db_size_bytes / (1024 * 1024), 2),
        "table_counts": table_counts,
        "query_latency_ms": query_latency_ms,
    }


def _collect_memory_stats() -> dict:
    stats: dict = {}
    try:
        import psutil
        proc = psutil.Process()
        mem = proc.memory_info()
        stats["rss_mb"] = round(mem.rss / (1024 * 1024), 1)
        stats["vms_mb"] = round(mem.vms / (1024 * 1024), 1)
    except (ImportError, Exception):
        pass

    stats["caches"] = {
        "news_translate_cache": len(news_tracker._translate_cache),
        "positions_active": len(position_tracker._positions),
        "whale_alerts": len(whale_tracker._whale_alerts),
        "ws_subscribed_symbols": len(ws_manager._manager._subscribed_symbols),
        "ws_subscribed_klines": len(ws_manager._manager._subscribed_klines),
        "event_buffer": len(event_recorder._buffer),
    }
    stats["event_recorder"] = {
        "buffer_size": len(event_recorder._buffer),
        "today_file_kb": round(event_recorder.get_today_file_size() / 1024, 1),
    }
    return stats


def _collect_process_stats() -> dict:
    return {
        "python_version": sys.version.split()[0],
        "pid": os.getpid(),
    }
