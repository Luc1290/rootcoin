from fastapi import APIRouter, Query

from backend.services import event_recorder, health_collector, log_buffer

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
async def get_health():
    return health_collector.get_health()


@router.get("/logs")
async def get_logs(limit: int = Query(100, le=500)):
    return {"logs": log_buffer.get_logs(limit), "errors": log_buffer.get_errors(50)}


@router.get("/events")
async def get_events(limit: int = Query(50, le=100)):
    return {"events": event_recorder.get_recent(limit)}


@router.get("/db")
async def get_db_stats():
    health = health_collector.get_health()
    return health.get("database", {})
