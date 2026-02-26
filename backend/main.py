from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.services import log_buffer
from backend.exchange.binance_client import close_client, init_client
from backend.core.database import close_db, init_db
from backend.exchange import symbol_filters, ws_manager
from backend.services import event_recorder, health_collector, news_tracker
from backend.trading import balance_tracker, position_tracker, price_recorder
from backend.market import (
    heatmap_manager, kline_manager, macro_tracker, market_analyzer,
    opportunity_detector, orderbook_tracker, whale_tracker,
)

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
        log_buffer.capture_processor,
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
    await event_recorder.start()
    await position_tracker.start()
    await price_recorder.start()
    await balance_tracker.start()
    await kline_manager.start()
    await macro_tracker.start()
    await whale_tracker.start()
    await orderbook_tracker.start()
    await heatmap_manager.start()
    await market_analyzer.start()
    await opportunity_detector.start()
    await news_tracker.start()
    await health_collector.start()
    log.info("rootcoin_started")
    yield
    log.info("rootcoin_stopping")
    await health_collector.stop()
    await news_tracker.stop()
    await opportunity_detector.stop()
    await market_analyzer.stop()
    await heatmap_manager.stop()
    await orderbook_tracker.stop()
    await whale_tracker.stop()
    await macro_tracker.stop()
    await kline_manager.stop()
    await balance_tracker.stop()
    await price_recorder.stop()
    await position_tracker.stop()
    await event_recorder.stop()
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
from backend.routes.api_orderbook import router as orderbook_router
from backend.routes.api_journal import router as journal_router
from backend.routes.api_opportunities import router as opportunities_router
from backend.routes.api_health import router as health_router
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
app.include_router(orderbook_router)
app.include_router(journal_router)
app.include_router(opportunities_router)
app.include_router(health_router)
app.include_router(ws_router)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
