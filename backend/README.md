# RootCoin Backend

The backend is a high-performance, asynchronous Python application built with **FastAPI**. It acts as the brain of RootCoin, managing real-time data from Binance, executing trading logic, and serving the frontend via REST and WebSockets.

## Core Architecture

### 1. Lifespan & Initialization (`main.py`)
The application follows a strict startup and shutdown sequence managed by FastAPI's `lifespan` context manager. It initializes 20+ modules in order, ensuring dependencies (like the database and Binance client) are ready before starting high-level services like the `trailing_manager` or `opportunity_detector`.

### 2. Data Management (`core/`)
- **Database (`database.py`)**: Uses **SQLAlchemy 2.0** with `aiosqlite` for asynchronous SQLite access.
- **Models (`models.py`)**: 11 ORM models covering positions, trades, orders, balance snapshots, price history, klines, opportunities, and AI analyses.
- **Config (`config.py`)**: Centralized settings using **Pydantic Settings**, loading environment variables from `.env` with type validation.

### 3. Exchange Layer (`exchange/`)
- **Binance Client**: A wrapper around `python-binance` AsyncClient.
- **WebSocket Manager (`ws_manager.py`)**: The central hub for real-time data. It manages multiple streams (User Data, Tickers, Klines) and dispatches events to internal subscribers.
- **Symbol Filters**: Caches Binance exchange information (`LOT_SIZE`, `PRICE_FILTER`) to validate orders before they are sent.

### 4. Trading Logic (`trading/`)
- **Position Tracker**: Reconstructs current positions by combining Binance wallet balances and local trade history. It manages the lifecycle of a position (Open -> Active -> Closed).
- **Order Manager**: Handles the complexity of placing Limit, Market, Stop Loss, Take Profit, and OCO orders.
- **Trailing Manager**: Implements sophisticated "Smart OCO" logic, automatically adjusting Stop Loss and Take Profit levels as the market moves in your favor.
- **PnL Engine (`pnl.py`)**: High-precision calculations using Python's `Decimal` type to avoid floating-point errors.

### 5. Market Intelligence (`market/` & `scoring/`)
- **Kline Manager**: Manages multi-timeframe OHLCV data and computes 14+ technical indicators (RSI, MACD, BB, etc.) on-the-fly.
- **Scoring Engine**: A 6-layer confluence system that aggregates technical signals, order flow (whales), and macro indicators into a single score.
- **LLM Analyzer**: Integrates with **Anthropic Claude** to provide deep, on-demand market analysis based on current technical context.
- **Whale Tracker**: Monitors the WebSocket feed for large `aggTrades` to detect institutional activity.

### 6. Services & Routes (`services/` & `routes/`)
- **Services**: Background tasks for Telegram notifications, news aggregation (RSS), price alerts, and system health monitoring.
- **Routes**: 19 specialized API routers providing clean endpoints for the frontend SPA.

## Key Principles

- **Event-Driven**: Most modules react to events dispatched by `ws_manager`, minimizing polling and CPU usage.
- **Precision First**: All financial data (prices, quantities, fees) uses `Decimal` for 100% accuracy.
- **Resiliency**: The system is designed to recover its full state from Binance upon restart, ensuring no data loss if the process crashes.
- **Structured Logging**: Uses `structlog` with JSON output, captured in a ring buffer for real-time viewing in the dashboard.
