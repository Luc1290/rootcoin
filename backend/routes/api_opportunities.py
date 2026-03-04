from fastapi import APIRouter, Query

from backend.market import opportunity_detector
from backend.services import opportunity_tracker

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


@router.get("")
async def get_opportunities():
    return {"opportunities": opportunity_detector.get_opportunities()}


@router.get("/history")
async def get_history(limit: int = Query(20, ge=1, le=100)):
    return {"history": await opportunity_tracker.get_history(limit)}


@router.get("/stats")
async def get_stats():
    return await opportunity_tracker.get_stats()
