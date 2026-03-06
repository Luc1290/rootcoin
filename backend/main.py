from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.services import log_buffer
from backend.exchange.binance_client import close_client, init_client
from backend.core.database import close_db, init_db
from backend.exchange import symbol_filters, ws_manager
from backend.services import event_recorder, health_collector, level_alert, news_tracker, opportunity_tracker, telegram_notifier
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
    started: list[str] = []
    try:
        await init_db(); started.append("db")
        await init_client(); started.append("client")
        await symbol_filters.init_filters(); started.append("filters")
        await ws_manager.start(); started.append("ws")
        await event_recorder.start(); started.append("events")
        await position_tracker.start(); started.append("positions")
        await price_recorder.start(); started.append("prices")
        await balance_tracker.start(); started.append("balances")
        await kline_manager.start(); started.append("klines")
        await macro_tracker.start(); started.append("macro")
        await whale_tracker.start(); started.append("whales")
        await orderbook_tracker.start(); started.append("orderbook")
        await heatmap_manager.start(); started.append("heatmap")
        await market_analyzer.start(); started.append("analyzer")
        await opportunity_detector.start(); started.append("opportunities")
        await opportunity_tracker.start(); started.append("opp_tracker")
        await news_tracker.start(); started.append("news")
        await health_collector.start(); started.append("health")
        await telegram_notifier.start(); started.append("telegram")
        await level_alert.start(); started.append("level_alert")
    except Exception:
        log.error("startup_failed", started=started, exc_info=True)
        await _shutdown(started)
        raise
    log.info("rootcoin_started")
    await telegram_notifier.notify_startup_summary()
    yield
    log.info("rootcoin_stopping")
    await _shutdown(started)
    log.info("rootcoin_stopped")


_SHUTDOWN_ORDER = [
    ("level_alert", level_alert.stop),
    ("telegram", telegram_notifier.stop),
    ("health", health_collector.stop),
    ("news", news_tracker.stop),
    ("opp_tracker", opportunity_tracker.stop),
    ("opportunities", opportunity_detector.stop),
    ("analyzer", market_analyzer.stop),
    ("heatmap", heatmap_manager.stop),
    ("orderbook", orderbook_tracker.stop),
    ("whales", whale_tracker.stop),
    ("macro", macro_tracker.stop),
    ("klines", kline_manager.stop),
    ("balances", balance_tracker.stop),
    ("prices", price_recorder.stop),
    ("positions", position_tracker.stop),
    ("events", event_recorder.stop),
    ("ws", ws_manager.stop),
    ("filters", symbol_filters.stop),
    ("client", close_client),
    ("db", close_db),
]


async def _shutdown(started: list[str]):
    for name, stop_fn in _SHUTDOWN_ORDER:
        if name in started:
            try:
                await stop_fn()
            except Exception:
                log.error("shutdown_error", module=name, exc_info=True)


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
from backend.routes.api_settings import router as settings_router
from backend.routes.api_alerts import router as alerts_router
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
app.include_router(settings_router)
app.include_router(alerts_router)
app.include_router(ws_router)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
