# RootCoin Frontend - Client-Side Intelligence

The RootCoin frontend is a performance-optimized Single Page Application (SPA) built with **Vanilla JavaScript** (ES6+). It translates raw WebSocket events from the backend into a high-fidelity trading dashboard.

## Architecture Highlights

### 1. Granular DOM Updates
Unlike React, which re-renders components, RootCoin uses direct DOM manipulation in `positions.js` and `app.js` to update only the specific HTML elements (like a price or PnL percentage) that have changed. This ensures smooth performance even on low-end mobile devices during periods of high market volatility.

### 2. The WebSocket Dispatcher (`websocket.js`)
The frontend's communication engine is built around a robust WebSocket client:
- **Event-to-Module Mapping**: It routes incoming JSON messages (e.g., `price_update`, `position_snapshot`, `balance_change`) to their respective UI modules (`positions.js`, `kline-chart.js`, `cockpit.js`).
- **Heartbeat & Recovery**: It maintains a constant heartbeat with the backend and includes automatic reconnection logic to ensure the dashboard never stays disconnected.

### 3. Financial Charting Suite
The dashboard features three levels of charting integration:
- **Main Chart (`kline-chart.js`)**: A professional-grade implementation using **TradingView's Lightweight Charts**. It supports real-time candle streaming, multiple timeframe switching, 14+ technical indicators, and interactive drawing (like price alerts).
- **Mini Sparklines (`charts.js`)**: Lightweight area charts used in the watchlist and cockpit to provide a 24h visual context for any symbol.
- **Trade Overlays (`cycles.js`)**: Automatically draws entry and exit markers, entry price lines, and "trade duration" shaded areas directly onto the main charts based on trade history data.

### 4. The Performance Journal (`journal.js`)
This module aggregates data from several API endpoints to build a comprehensive view of trading history:
- **Equity Curve**: A multi-series chart showing portfolio value vs. cumulative PnL.
- **PnL Heatmap**: A GitHub-style calendar grid visualization of daily profits and losses.
- **Trade Timeline**: A rich, chronologically ordered feed of every trade, complete with its market context at the time of entry.

## Styling & Theme

RootCoin uses **Tailwind CSS v3** with a carefully crafted dark theme tailored for trading:
- **Responsive Breakpoints**: Custom grid layouts for mobile (sm), tablet (md), and desktop (lg).
- **Interactive Feedback**: Modal windows for order confirmation, sliding sidebars for logs, and real-time toast notifications for trade fills.
- **Color Coding**: Consistent usage of "Binance-standard" colors (Success/Green for Long/Profit, Danger/Red for Short/Loss) across the entire UI.

## Technical Stack Detail

| Component | Library / Tech | Purpose |
|-----------|----------------|---------|
| **Core UI** | Vanilla JS / DOM API | High-performance, no-build SPA |
| **Styling** | Tailwind CSS v3 | Rapid, utility-first styling |
| **Charting** | Lightweight Charts 4.2.2 | Financial data visualization |
| **Icons** | Inline SVG | Scalable, no external asset dependencies |
| **Storage** | Browser LocalStorage | Persisting tab selection and chart preferences |
