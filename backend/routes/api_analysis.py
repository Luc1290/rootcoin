from fastapi import APIRouter, HTTPException

from backend.market import market_analyzer

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("")
async def get_all_analyses():
    return market_analyzer.get_all_analyses()


@router.get("/{symbol}")
async def get_analysis(symbol: str):
    analysis = market_analyzer.get_analysis(symbol)
    if not analysis:
        raise HTTPException(404, f"No analysis for {symbol}")
    return analysis
