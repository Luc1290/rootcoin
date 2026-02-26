from fastapi import APIRouter

from backend.services import news_tracker

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("")
async def get_news():
    return news_tracker.get_news()
