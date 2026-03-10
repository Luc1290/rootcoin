# RootCoin Frontend

The frontend is a lightweight Single Page Application (SPA) designed for speed, responsiveness, and minimal overhead. It is built using **Vanilla JavaScript**, **Tailwind CSS**, and served directly by the Python backend.

## Core Architecture

### 1. SPA Navigation (`app.js`)
The application uses a custom tab-based navigation system. Each tab (Cockpit, Positions, Analysis, Heatmap, etc.) is a separate section in the `index.html` file, controlled by `app.js`. This approach eliminates page reloads and provides a smooth, application-like experience.

### 2. Real-time Communication (`websocket.js`)
RootCoin relies on a persistent WebSocket connection to the backend. The frontend subscribes to real-time events, ensuring the UI (prices, positions, orders, and charts) is always up-to-date without manual refreshes. It includes automatic reconnection logic and heartbeat monitoring.

### 3. Modular UI Components (`js/`)
The JavaScript logic is divided into functional modules, each responsible for a specific part of the dashboard:
- **`positions.js` & `position-cards.js`**: Build and manage real-time position cards with PnL updates and action modals.
- **`kline-chart.js`**: A full-featured interactive candlestick chart using **TradingView's Lightweight Charts**, supporting 14 technical indicators.
- **`charts.js` & `mini-trade-chart.js`**: Small, efficient sparkline charts used for watchlists and opportunity lists.
- **`journal.js`**: Visualizes trading performance using an equity curve, a PnL calendar (GitHub-style), and a detailed trade timeline.
- **`health.js`**: Provides real-time status monitoring for all 19+ backend modules and system metrics.

## Styling & Design

- **Tailwind CSS**: The entire UI is styled using **Tailwind CSS v3**. It uses a dark-first theme optimized for night trading.
- **Mobile-First**: Every button, card, and chart is designed to be touch-friendly, making the dashboard fully usable on smartphones and tablets.
- **PWA Ready**: Includes a `manifest.json` and high-quality icons, allowing it to be installed on Android or iOS home screens as a standalone application.

## Build Process

The frontend uses a **no-build** approach for JavaScript, meaning all JS files are loaded directly by the browser as ES modules.

For CSS, the only requirement is to compile Tailwind:
```bash
npx tailwindcss -i css/input.css -o css/output.css --minify
```

## Tech Stack

| Component | Library |
|-----------|---------|
| **Core** | Vanilla JS (ES6+) |
| **Styling** | Tailwind CSS v3 |
| **Charts** | Lightweight Charts (TradingView) |
| **Icons** | Custom SVGs |
| **Data Flow** | WebSocket + REST API |
