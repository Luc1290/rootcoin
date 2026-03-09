from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.market import llm_analyzer

router = APIRouter(prefix="/api/llm", tags=["llm"])


class AnalyzeRequest(BaseModel):
    symbol: str


@router.post("/analyze")
async def analyze(req: AnalyzeRequest):
    try:
        result = await llm_analyzer.analyze(req.symbol)
    except Exception as e:
        raise HTTPException(500, str(e))
    if "error" in result and "direction" not in result:
        raise HTTPException(500, result["error"])
    return result


@router.get("/preview/{symbol}")
async def preview_prompt(symbol: str):
    try:
        prompt = await llm_analyzer.build_prompt(symbol)
        return {"symbol": symbol, "prompt": prompt, "system": llm_analyzer.SYSTEM_PROMPT}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/last")
async def get_last(symbol: str | None = None):
    result = llm_analyzer.get_last_analysis(symbol)
    if not result:
        return {"analysis": None}
    return result


@router.get("/history")
async def history(symbol: str | None = None, limit: int = 30):
    return {"history": await llm_analyzer.get_history(symbol, limit)}


@router.get("/stats")
async def stats(symbol: str | None = None):
    return await llm_analyzer.get_stats(symbol)
