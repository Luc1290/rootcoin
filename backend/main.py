from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend import (
    balance_tracker, heatmap_manager, kline_manager, macro_tracker,
    market_analyzer, news_tracker, position_tracker, price_recorder,
    whale_tracker, ws_manager,
)
from backend.binance_client import close_client, init_client
from backend.database import close_db, init_db
from backend.utils import symbol_filters

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger()

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("rootcoin_starting")
    await init_db()
    await init_client()
    await symbol_filters.init_filters()
    await ws_manager.start()
    await position_tracker.start()
    await price_recorder.start()
    await balance_tracker.start()
    await kline_manager.start()
    await macro_tracker.start()
    await whale_tracker.start()
    await heatmap_manager.start()
    await market_analyzer.start()
    await news_tracker.start()
    log.info("rootcoin_started")
    yield
    log.info("rootcoin_stopping")
    await news_tracker.stop()
    await market_analyzer.stop()
    await heatmap_manager.stop()
    await whale_tracker.stop()
    await macro_tracker.stop()
    await kline_manager.stop()
    await balance_tracker.stop()
    await price_recorder.stop()
    await position_tracker.stop()
    await ws_manager.stop()
    await symbol_filters.stop()
    await close_client()
    await close_db()
    log.info("rootcoin_stopped")


app = FastAPI(title="RootCoin", lifespan=lifespan)

from backend.routes.dashboard import router as dashboard_router
from backend.routes.api_positions import router as positions_router
from backend.routes.api_orders import router as orders_router
from backend.routes.api_balances import router as balances_router
from backend.routes.api_trades import router as trades_router
from backend.routes.api_cycles import router as cycles_router
from backend.routes.api_prices import router as prices_router
from backend.routes.api_portfolio import router as portfolio_router
from backend.routes.api_klines import router as klines_router
from backend.routes.api_analysis import router as analysis_router
from backend.routes.api_heatmap import router as heatmap_router
from backend.routes.api_news import router as news_router
from backend.routes.ws_dashboard import router as ws_router

app.include_router(dashboard_router)
app.include_router(positions_router)
app.include_router(orders_router)
app.include_router(balances_router)
app.include_router(trades_router)
app.include_router(cycles_router)
app.include_router(prices_router)
app.include_router(portfolio_router)
app.include_router(klines_router)
app.include_router(analysis_router)
app.include_router(heatmap_router)
app.include_router(news_router)
app.include_router(ws_router)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
