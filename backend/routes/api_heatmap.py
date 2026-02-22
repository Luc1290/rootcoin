from fastapi import APIRouter, Query

from backend import heatmap_manager

router = APIRouter(prefix="/api/heatmap", tags=["heatmap"])


@router.get("")
async def get_heatmap(limit: int = Query(50, le=200), window: str = Query("4h")):
    await heatmap_manager.ensure_window_data(window)
    return heatmap_manager.get_heatmap_data(limit, window)
