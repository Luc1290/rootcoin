<p align="center">
  <img src="frontend/logo.svg" alt="RootCoin" width="420" />
</p>

<h3 align="center">Personal Trading Dashboard for Binance</h3>

<p align="center">
  Real-time position tracking, order management, market analysis, and AI-powered insights — all in one self-hosted dashboard.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=white" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/FastAPI-0.129-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Binance-spot%20%2B%20margin-F0B90B?logo=binance&logoColor=white" alt="Binance" />
  <img src="https://img.shields.io/badge/Tailwind%20CSS-v3-38BDF8?logo=tailwindcss&logoColor=white" alt="Tailwind CSS" />
  <img src="https://img.shields.io/badge/Claude-Opus%204-blueviolet?logo=anthropic&logoColor=white" alt="Claude AI" />
</p>

---

## Overview

RootCoin is a self-hosted trading dashboard designed for a single trader on Binance. It runs 24/7 on a VPS and provides a mobile-first web interface accessible from any device.

---

## Project Documentation

Detailed technical documentation is available for each layer of the project:

- 🧠 **[Backend Documentation](backend/README.md)**: FastAPI architecture, async modules, trading logic, and database models.
- 🎨 **[Frontend Documentation](frontend/README.md)**: SPA architecture, Vanilla JS modules, WebSocket client, and charts.
- 🛠️ **[Scripts Documentation](scripts/README.md)**: VPS setup, automated deployment, and maintenance scripts.

---

## 🔒 Security

RootCoin is designed for personal use and **does not include a built-in authentication system**. It is highly recommended to:

1. **Use a VPN**: Access the dashboard only via [Tailscale](https://tailscale.com/) or a similar secure VPN.
2. **API Keys**: Never share or commit your `.env` file. Ensure your Binance API keys have only the necessary permissions (Spot/Margin Trading, but **strictly NO Withdrawals**).
3. **VPS Hardening**: Disable root login and use SSH keys for server access.

---

## Features

### Position Management
- **Live tracking** — Positions reconstructed from Binance balances + trade history (spot/margin don't have native positions)
- **Order placement** — Stop Loss, Take Profit, OCO orders with Binance filter validation (LOT_SIZE, PRICE_FILTER, MIN_NOTIONAL)
- **Smart trailing** — Automatic OCO on new positions using key levels (0.6-1% SL), breakeven at +0.5% (covers real fees), capital-aware gain lock, trailing snaps to key levels (never retreats), TP guard, stale tightening, naked recovery, emergency close, manual override detection, max risk 1.5% of capital per trade
- **One-click close** — Close any position directly from the dashboard
- **Crash recovery** — Full state reconstruction from Binance on restart, no duplicates

### Charting
- **Candlestick chart** — Full OHLCV with interval switching (1m to 1M)
- **14 technical indicators** — SMA, EMA, RSI, Bollinger Bands, MACD, OBV, Stochastic RSI, ATR, VWAP, ADX, MFI, Buy/Sell Pressure, and more
- **Trade markers** — Buy/sell fills displayed on chart with entry price line
- **Cycle overlay** — Colored areas showing position lifecycle (open to close)
- **Custom price alerts** — Set alert lines on the chart, get notified via Telegram when crossed
- **Live WebSocket updates** — Candles update in real-time

### Market Analysis
- **Unified scoring** — 6-layer confluence scoring across 5m, 15m, 1h, 4h timeframes + order flow + macro
- **Key levels** — Pivot points, swing highs/lows, Fibonacci retracements, psychological levels
- **Timing coach** — Entry timing evaluation (level retest, MACD confirmation, RSI, spread, session)
- **Opportunity detection** — Filters high-score symbols without open positions, with cooldown
- **AI analysis** — On-demand Claude Opus analysis with multi-timeframe context, self-improving via track record

### Market Data
- **Crypto heatmap** — Top 50 USDC pairs by 24h volume, colored by 4h price variation
- **Whale tracker** — Detects large trades on Binance (aggTrades above threshold)
- **Order book** — Depth data with bid/ask imbalance, wall detection, spread
- **Macro indicators** — DXY, VIX, Nasdaq, Gold, US10Y, US5Y, Oil, USD/JPY via yfinance
- **News feed** — RSS from CoinDesk + Google News (crypto + macro), translated to French

### Trading Journal
- **Equity curve** — Portfolio value over time with drawdown visualization
- **Calendar heatmap** — Daily PnL in a GitHub-style calendar grid
- **Trade timeline** — Every trade with market context snapshot (indicators, macro, order book state at entry)

### Monitoring
- **System health** — Live status of all 19+ modules, WebSocket heartbeat, DB stats, memory usage
- **Log terminal** — Real-time structured log viewer with level filtering
- **Event inspector** — Raw WebSocket events from Binance (JSONL, 7-day retention)
- **Telegram notifications** — Configurable alerts for positions, orders, and price levels

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                         VPS (systemd)                         │
│                                                               │
│   ┌──────────┐     WebSocket      ┌────────────────────────┐  │
│   │ Binance  │◄──────────────────►│    Python Backend      │  │
│   │ API / WS │                    │    (FastAPI + async)    │  │
│   └──────────┘                    │                        │  │
│                                   │  position_tracker      │  │
│                                   │  order_manager         │  │
│   ┌──────────┐                    │  market_analyzer       │  │
│   │  SQLite  │◄──────────────────►│  scoring engine        │  │
│   │          │                    │  whale/orderbook/macro  │  │
│   └──────────┘                    │  trailing_manager      │  │
│                                   │  telegram_notifier     │  │
│   ┌──────────┐                    │  llm_analyzer          │  │
│   │ Claude   │◄──────────────────►│                        │  │
│   │ Opus API │                    └───────────┬────────────┘  │
│   └──────────┘                                │               │
│                                          HTTP + WS            │
│   ┌──────────┐                                │               │
│   │ Telegram │◄───────────────────────────────┤               │
│   │ Bot API  │                    ┌───────────▼────────────┐  │
│   └──────────┘                    │   Web Dashboard (SPA)  │  │
│                                   │   HTML / JS / Tailwind │  │
│                                   └────────────────────────┘  │
│                                               ▲               │
└───────────────────────────────────────────────┼───────────────┘
                                                │
                                           Tailscale VPN
                                                │
                                  ┌─────────────┼─────────────┐
                                  │             │             │
                               PC/Mac       iPhone        Tablet
```

### Event-Driven Core

The backend is built around a central event dispatcher (`ws_manager`). Modules subscribe to events and react independently:

```
Binance WS ──► ws_manager ──dispatch──► position_tracker  (update positions in memory)
                                     ├► price_recorder     (persist prices to DB)
                                     ├► balance_tracker    (snapshot balances)
                                     ├► kline_manager      (update candlestick data)
                                     ├► whale_tracker      (detect large trades)
                                     ├► level_alert        (check price level crossings)
                                     └► ws_dashboard       (broadcast to frontend clients)
```

### Startup Sequence

The application initializes 19+ modules in a specific order during FastAPI lifespan:

1. Database tables + Binance client + symbol filter cache
2. WebSocket streams (user data, prices, token refresh)
3. Position reconstruction from Binance state
4. Market data collectors (klines, macro, whales, order book, heatmap)
5. Analysis engine (scoring, opportunities, news)
6. Services (health, Telegram, trailing, alerts)

Shutdown happens in reverse order. Each module exposes `start()` / `stop()`.

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| **Backend** | Python 3.12+ / FastAPI | Async-native, WebSocket support, lightweight |
| **Exchange** | python-binance 1.0.35 | Best spot + margin support, up-to-date WebSocket |
| **Database** | SQLite + SQLAlchemy + aiosqlite | No separate DB server, sufficient for single user |
| **Frontend** | Vanilla HTML/CSS/JS | No build step, served directly by FastAPI |
| **Styling** | Tailwind CSS v3 | Utility-first, responsive, dark theme |
| **Charts** | LightweightCharts 4.2.2 | Fast candlestick rendering, sub-charts |
| **Logging** | structlog (JSON) | Structured, machine-readable, ring buffer |
| **Macro Data** | yfinance | DXY, VIX, indices, bonds, commodities |
| **AI** | Anthropic Claude Opus | On-demand multi-timeframe analysis |
| **Notifications** | Telegram Bot API (httpx) | Position/order/level alerts |
| **Deployment** | systemd + Tailscale | 24/7 service, secure remote access |

---

## Getting Started

### Prerequisites

- Python 3.12+
- Node.js (for Tailwind CSS compilation only)
- A Binance account with API keys (spot + margin enabled)
- *(Optional)* Telegram bot token for notifications
- *(Optional)* Anthropic API key for AI analysis

### Installation

```bash
git clone https://github.com/your-username/rootcoin.git
cd rootcoin
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or: venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

### Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Required
BINANCE_API_KEY=your_api_key
BINANCE_SECRET_KEY=your_api_secret

# Stablecoins excluded from position detection (quote currencies)
STABLECOINS=USDT,USDC,BUSD,DAI,TUSD,FDUSD

# Symbols always tracked (majors). Open positions are added automatically.
DEFAULT_WATCHLIST=BTCUSDC,ETHUSDC,BNBUSDC

# Intervals (seconds)
BALANCE_SNAPSHOT_INTERVAL=300
PRICE_RECORD_INTERVAL=60

# Server
PORT=8001

# Data retention
PRICE_RETENTION_DAYS=30

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json

# Optional — Telegram notifications
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

### Compile Tailwind CSS

```bash
npx -y tailwindcss@3 -i frontend/css/input.css -o frontend/css/output.css --minify
```

### Run Locally

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload
```

Open `http://localhost:8001` in your browser.

---

## Deployment (VPS)

### systemd Service

A service file is provided in `scripts/rootcoin.service`:

```ini
[Unit]
Description=RootCoin Trading Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=rootcoin
WorkingDirectory=/home/rootcoin/rootcoin
ExecStart=/home/rootcoin/rootcoin/venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8001
Restart=always
RestartSec=5
EnvironmentFile=/home/rootcoin/rootcoin/.env

[Install]
WantedBy=multi-user.target
```

### Setup

```bash
# Copy service file
sudo cp scripts/rootcoin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable rootcoin
sudo systemctl start rootcoin

# View logs
journalctl -u rootcoin -f
```

### Deploy Updates

```bash
git pull && sudo systemctl restart rootcoin
```

### Remote Access

Use [Tailscale](https://tailscale.com/) to securely access the dashboard from any device without exposing the port to the internet.

---

## Project Structure

```
rootcoin/
├── backend/
│   ├── main.py                 # FastAPI entry point + lifespan
│   ├── core/
│   │   ├── config.py           # Pydantic settings from .env
│   │   ├── database.py         # SQLAlchemy async engine + migrations
│   │   └── models.py           # 11 ORM models
│   ├── exchange/
│   │   ├── binance_client.py   # Binance AsyncClient wrapper
│   │   ├── ws_manager.py       # WebSocket streams + event dispatcher
│   │   └── symbol_filters.py   # LOT_SIZE / PRICE_FILTER cache
│   ├── trading/
│   │   ├── pnl.py              # Pure Decimal PnL calculations
│   │   ├── position_tracker.py # Position state machine
│   │   ├── position_reconciler.py  # Startup + periodic reconciliation
│   │   ├── order_manager.py    # SL / TP / OCO placement
│   │   ├── trailing_manager.py # Smart OCO trailing
│   │   ├── balance_tracker.py  # Balance snapshots
│   │   └── price_recorder.py   # Price history persistence
│   ├── scoring/
│   │   ├── signal_engine.py    # Multi-timeframe signal extraction
│   │   ├── scorer.py           # 6-layer confluence scoring
│   │   └── timing_coach.py     # Entry timing evaluation
│   ├── market/
│   │   ├── kline_manager.py    # OHLCV + 14 technical indicators
│   │   ├── macro_tracker.py    # DXY, VIX, indices (yfinance)
│   │   ├── whale_tracker.py    # Large trade detection
│   │   ├── orderbook_tracker.py    # Depth + imbalance
│   │   ├── heatmap_manager.py  # Top 50 crypto heatmap
│   │   ├── market_analyzer.py  # Analysis orchestrator
│   │   ├── opportunity_detector.py # High-score symbol filter
│   │   ├── llm_analyzer.py     # Claude AI analysis
│   │   └── analysis_formatter.py   # Signal formatting
│   ├── services/
│   │   ├── event_recorder.py   # Raw WS events → JSONL
│   │   ├── journal_snapshotter.py  # Market context at trade time
│   │   ├── log_buffer.py       # structlog ring buffer
│   │   ├── news_tracker.py     # RSS feeds (CoinDesk + Google)
│   │   ├── health_collector.py # System health aggregation
│   │   ├── telegram_notifier.py    # Telegram bot integration
│   │   ├── level_alert.py      # Price level crossing alerts
│   │   └── opportunity_tracker.py  # Opportunity lifecycle
│   └── routes/                 # 18 API routers (REST + WebSocket)
│
├── frontend/
│   ├── index.html              # SPA — 10 tabs
│   ├── manifest.json           # PWA metadata
│   ├── logo.svg                # Brand logo
│   ├── css/
│   │   ├── input.css           # Tailwind source
│   │   ├── output.css          # Tailwind compiled
│   │   └── style.css           # Custom styles
│   └── js/
│       ├── websocket.js        # WS client + auto-reconnect
│       ├── app.js              # Tab orchestrator
│       ├── utils.js            # Shared helpers
│       ├── positions.js        # Position cards + modals
│       ├── position-cards.js   # Card HTML builder
│       ├── cycles.js           # Closed cycles
│       ├── trades.js           # Trade history
│       ├── balances.js         # Balances + portfolio chart
│       ├── charts.js           # Mini price charts
│       ├── kline-chart.js      # Candlestick + indicators
│       ├── mini-trade-chart.js # Inline opportunity charts
│       ├── analysis.js         # Market analysis page
│       ├── heatmap.js          # Crypto heatmap
│       ├── opportunities.js    # Opportunity cards
│       ├── alerts.js           # Custom price alerts
│       ├── cockpit.js          # Dashboard summary
│       ├── journal.js          # Equity + calendar + timeline
│       └── health.js           # System health monitor
│
├── scripts/
│   ├── rootcoin.service        # systemd unit file
│   ├── setup_vps.sh            # VPS initial setup
│   ├── deploy.sh               # Automated deployment
│   └── backup_db.sh            # Database backup
│
├── .env.example                # Environment template
├── requirements.txt            # Python dependencies
├── tailwind.config.js          # Tailwind CSS config
└── .gitignore
```

---

## API Endpoints

### REST API

| Group | Endpoints | Description |
|-------|-----------|-------------|
| **Positions** | `GET/POST/DELETE /api/positions/*` | CRUD positions, place SL/TP/OCO, close |
| **Orders** | `DELETE /api/orders/{id}` | Cancel an order |
| **Balances** | `GET /api/balances`, `/api/balances/history` | Current balances + history |
| **Trades** | `GET /api/trades` | Trade history (filter by symbol) |
| **Cycles** | `GET /api/cycles`, `/api/cycles/stats` | Closed/open cycles + win rate stats |
| **Prices** | `GET /api/prices/history`, `/api/prices/current` | OHLC history + live prices |
| **Portfolio** | `GET /api/portfolio/history` | Aggregated portfolio value over time |
| **Klines** | `GET /api/klines/{symbol}` | OHLCV + computed indicators |
| **Analysis** | `GET /api/analysis`, `/api/analysis/{symbol}` | Market analysis with levels + macro |
| **Heatmap** | `GET /api/heatmap` | Top 50 crypto by volume, 4h variation |
| **Order Book** | `GET /api/orderbook/{symbol}` | Depth, imbalance, walls, spread |
| **Opportunities** | `GET /api/opportunities` | Detected entry opportunities |
| **News** | `GET /api/news` | RSS feed (CoinDesk + Google News) |
| **Journal** | `GET /api/journal/calendar`, `/equity`, `/entries` | PnL calendar, equity curve, timeline |
| **Health** | `GET /api/health` | Module status, DB stats, memory |
| **Alerts** | `GET/POST/DELETE /api/alerts` | Custom price alerts |
| **AI** | `POST /api/llm/analyze`, `GET /api/llm/history` | Claude analysis + track record |
| **Settings** | `GET/PUT /api/settings/*` | App settings + Telegram config |

### WebSocket

`WS /ws` — Real-time broadcast:
- `positions_snapshot` every 2 seconds
- Price updates, order events, balance changes
- Analysis updates, opportunity alerts

---

## Technical Indicators

All indicators are computed on-the-fly from stored klines (~5-10ms for 1000 candles). Request only what you need:

```
GET /api/klines/BTCUSDC?indicators=ma,rsi,bb,macd
```

| Key | Indicator | Description |
|-----|-----------|-------------|
| `ma` | SMA | Simple Moving Average (7, 25, 99) |
| `ema` | EMA | Exponential Moving Average (7, 21, 50) |
| `rsi` | RSI | Relative Strength Index (14, Wilder) |
| `bb` | Bollinger | Bands (SMA 20 +/- 2 std) |
| `macd` | MACD | (12, 26, 9) — line, signal, histogram |
| `obv` | OBV | On-Balance Volume |
| `buy_sell` | Pressure | Taker buy % - 50 |
| `stoch_rsi` | Stoch RSI | (14, 3, 3) — K, D |
| `atr` | ATR | Average True Range (14) |
| `vwap` | VWAP | Volume Weighted Average Price |
| `adx` | ADX | Average Directional Index (14) |
| `mfi` | MFI | Money Flow Index (14) |

---

## Scoring System

The opportunity detection uses a **6-layer confluence scoring** system:

| Layer | Weight | Source |
|-------|--------|--------|
| 5-minute | 30 pts | RSI, MACD, structure (rejections, break-retest) |
| 15-minute | 25 pts | Trend confirmation, key level tests |
| 1-hour | 15 pts | Higher timeframe bias |
| 4-hour | 5 pts | Macro trend direction |
| Order flow | 20 pts | Whale activity, order book imbalance |
| Macro | -5 to +5 pts | DXY, VIX, indices correlation |

Symbols scoring above the threshold (without an open position) trigger opportunity alerts with entry timing evaluation.

---

## Design Principles

- **Decimal everywhere** — All prices, quantities, and PnL use Python `Decimal`. Never `float` for money.
- **Binance filter validation** — Every order is validated against `LOT_SIZE`, `PRICE_FILTER`, and `MIN_NOTIONAL` before submission.
- **UTC in database** — All timestamps stored in UTC. Conversion to local time happens only in the frontend.
- **Event-driven** — Modules subscribe to events, no polling loops where WebSocket events are available.
- **Crash recovery** — Full state reconstruction from Binance on restart. Upsert on Binance IDs prevents duplicates.
- **Mobile-first** — Touch-friendly (44px+ tap targets), responsive via Tailwind breakpoints (sm / md / lg).

---

## PWA Support

RootCoin can be installed as a Progressive Web App on iOS and Android:

1. Open the dashboard URL in Safari (iOS) or Chrome (Android)
2. Tap "Add to Home Screen"
3. The app runs in standalone mode with the dark theme

---

## License

This project is not currently licensed for redistribution. All rights reserved.
