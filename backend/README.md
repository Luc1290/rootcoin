# RootCoin Backend - Technical Deep Dive

The RootCoin backend is an event-driven, asynchronous engine designed for high-precision trading. It manages the entire lifecycle of a trade, from opportunity detection to automated trailing exit.

## 🤖 AI Intelligence (LLM Analyzer)

RootCoin features a sophisticated AI analysis module powered by **Anthropic Claude Opus**, designed to act as a professional co-trader.

### 1. Multidimensional Context Injection
The `llm_analyzer.py` doesn't just send a simple question. It builds a massive, data-rich prompt containing:
- **Technical Signals**: Full OHLCV and indicators (RSI, MACD, BB, EMA) across 4 timeframes (5m, 15m, 1h, 4h).
- **Market Sentiment**: Order book imbalance (Bids/Asks), recent Whale activity, and volume profiles.
- **Macro Overlay**: Real-time values and trends for DXY, VIX, Nasdaq, and US10Y treasury yields.
- **Self-Awareness**: A summary of the AI's recent performance (track record) is included, encouraging self-correction and objective analysis.

### 2. Structured Decision Making
Claude is instructed to produce a strict JSON output, allowing the system to:
- **Automate Trade Setups**: Automatically extract suggested Entry, SL, and TP levels.
- **Risk Evaluation**: Calculate the Risk/Reward ratio and confidence score (0-100) before presenting the analysis to the user.
- **Market Reading**: Extract a concise "Key Signal" and "Invalidation Point" to help the user understand the *why* behind the trade.

### 3. Performance Tracking & Life Cycle
Analyses are stored in the `llm_analyses` table and monitored in real-time:
- **Outcome Detection**: The system tracks live prices to mark analyses as `tp1_hit`, `tp2_hit`, or `sl_hit`.
- **Superseding Logic**: Old analyses are automatically marked as `superseded` when new data renders them obsolete, preventing the use of stale information.
- **Cost & Efficiency**: Tracks token usage (`input_tokens`/`output_tokens`) to monitor API costs while ensuring the highest quality of reasoning.

---

## 核心 (Core) Mechanisms

### 1. State Reconstruction & Position Tracking
Since Binance Spot and Margin do not have a native "Position" object (only balances), RootCoin reconstructs them in real-time:
- **`position_tracker.py`**: Combines WebSocket `executionReport` events with current wallet balances.
- **DCA Logic**: Automatically detects multiple fills and manual buys on the same symbol to update the weighted average entry price.
- **Commission Handling**: Accurately converts commissions (even in BNB or other assets) to USD using live prices to calculate **Net PnL**.
- **Residual Cleanup**: After closing a Margin position, it automatically detects and sells tiny "dust" residuals or repays small remaining loans caused by commission fluctuations.

### 2. Smart OCO Trailing Manager
The `trailing_manager.py` is one of the most complex modules. It implements a non-linear trailing logic:
- **Level-Aware**: Instead of a fixed percentage, it snaps SL/TP levels to technical key levels (support/resistance) found by the `market_analyzer`.
- **Activation & Breakeven**: Moves SL to breakeven (covering all entry/exit fees + a $5 cushion) only after a configurable profit threshold is reached.
- **TP Guard**: If the price approaches the Take Profit level (e.g., within 0.3%), it forces a protective SL move to lock in gains.
- **Manual Override Detection**: If you manually move an order on Binance, the manager detects the `orderListId` change and pauses auto-trailing for that position to respect your decision.

### 3. The 6-Layer Scoring Engine
The `scorer.py` uses a weighted confluence model to generate a unified score (0-100):
1. **L0: 5m Primary (30 pts)**: Trend, momentum (RSI/MACD), and market structure.
2. **L1: 15m Confirmation (25 pts)**: Validates the 5m signal on a higher timeframe.
3. **L2: 1h Context (15 pts)**: Overall trend bias.
4. **L3: 4h Warning (5 pts)**: Major trend direction.
5. **L4: Real-time Flow (20 pts)**: Orderbook imbalance, Buy/Sell pressure, and recent Whale alerts (AggTrades).
6. **L5: Macro Context (+/- 5 pts)**: Correlates with DXY, VIX, Nasdaq, Gold, and US10Y.

### 4. High-Precision Calculations
- **Decimal Everywhere**: Every calculation uses Python's `Decimal` type. Floating point math is strictly forbidden to prevent "penny errors" in PnL and order quantities.
- **Filter Validation**: All orders are passed through `symbol_filters.py` to ensure they meet Binance's `LOT_SIZE`, `PRICE_FILTER`, and `MIN_NOTIONAL` requirements before submission.

## Data Flow & WebSockets
- **`ws_manager.py`**: Manages two distinct connections:
    - **User Stream**: Uses `userDataStream.subscribe.listenToken` for private events (fills, balance changes). Includes an auto-refresh task for the listenKey every 30 minutes.
    - **Price Stream**: Aggregates multiple ticker and kline streams into a single multiplexed connection.
- **Event Recorder**: Every raw WebSocket message is archived to JSONL files (7-day retention) for post-trade analysis or debugging.

## Technical Indicators
Indicators are computed in `kline_manager.py` using optimized pure-Python implementations. This allows for:
- **Instant updates**: Indicators are recalculated as soon as a new candle closes or a price update is received.
- **Dynamic requested sets**: The API `/api/klines` allows the frontend to request only the specific indicators needed for the current view.
