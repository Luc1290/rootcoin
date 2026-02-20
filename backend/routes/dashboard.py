from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@router.get("/", response_class=HTMLResponse)
async def index():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return index_file.read_text(encoding="utf-8")
    return "<h1>RootCoin</h1><p>Frontend not found.</p>"
