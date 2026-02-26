from fastapi import APIRouter

from backend.market import opportunity_detector

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


@router.get("")
async def get_opportunities():
    return {"opportunities": opportunity_detector.get_opportunities()}
