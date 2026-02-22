import re
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

_cache_bust = str(int(time.time()))
_html_cache: str | None = None

_STATIC_RE = re.compile(r'(/static/(?:js|css)/[^"\']+\.(js|css))')


def _build_html() -> str:
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        return "<h1>RootCoin</h1><p>Frontend not found.</p>"
    html = index_file.read_text(encoding="utf-8")
    return _STATIC_RE.sub(rf"\1?v={_cache_bust}", html)


@router.get("/", response_class=HTMLResponse)
async def index():
    global _html_cache
    if _html_cache is None:
        _html_cache = _build_html()
    return _html_cache
