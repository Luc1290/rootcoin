# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

Application de trading avec dashboard web permettant de :
- Tracker en temps réel les positions ouvertes sur Binance (spot + margin cross/isolated)
- Afficher PnL, prix d'entrée, prix actuel, durée de la position
- Poser des ordres SL, TP, OCO, fermer ou annuler les ordres depuis le dashboard
- Stocker l'historique des trades, balances, et prix des tokens
- Tourner 24/7 sur un VPS accessible via Tailscale depuis n'importe quel appareil.
- Voir `doc/private/VPS.md` pour les commandes VPS, DB, et scripts de maintenance.
- Schema db dans `doc/private/Project.md` ou curl directement le vps pour etre a jour.

## Commandes

- **Dev local** : `uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload`
- **Installer les deps Python** : `pip install -r requirements.txt`
- **VPS deploy** : `git pull && sudo systemctl restart rootcoin`
- **Logs VPS** : `journalctl -u rootcoin -f`

## Architecture

### Flux de donnees temps reel

```
Binance WS ──► ws_manager.py ──dispatch──► position_tracker.py (met a jour _positions en memoire)
                                        ├► price_recorder.py (ecrit en DB periodiquement)
                                        ├► balance_tracker.py (snapshots en DB)
                                        └► routes/ws_dashboard.py (broadcast aux clients frontend)
```

### Singletons et cycle de vie

Le demarrage (`main.py` lifespan) initialise dans cet ordre :
1. `init_db()` — cree les tables SQLite si absentes
2. `init_client()` — singleton `AsyncClient` python-binance (`binance_client._client`)
3. `symbol_filters.init_filters()` — cache `exchangeInfo` (LOT_SIZE, PRICE_FILTER, MIN_NOTIONAL)
4. `ws_manager.start()` — lance 3 tasks async : user data stream, price stream, token refresh
5. `event_recorder.start()` — cree dossier JSONL, cleanup fichiers > 7 jours
6. `position_tracker.start()` — scan Binance pour reconstruire les positions, puis ecoute les events WS
7. `price_recorder.start()` / `balance_tracker.start()` — s'abonnent aux events WS
8. `kline_manager.start()` — cleanup periodique des vieilles klines
9. `macro_tracker.start()` — fetch macro (DXY, VIX, SPX, Gold) via yfinance, refresh 5 min
10. `whale_tracker.start()` — poll Binance aggTrades pour gros mouvements
11. `orderbook_tracker.start()` — poll Binance depth, calcul imbalance/walls/spread
12. `heatmap_manager.start()` — fetch top 50 USDC par volume 24h, variation sur fenetre 4h glissante
13. `market_analyzer.start()` — orchestrateur analyse : delegue a `scoring/signal_engine` + `scoring/scorer` pour scoring unifie par confluence
14. `opportunity_detector.start()` — filtre symboles sans position avec score unifie > seuil, messages FR, cooldown
15. `news_tracker.start()` — fetch RSS CoinDesk + Google News, traduction EN→FR, cache memoire
16. `health_collector.start()` — collecte sante modules, DB stats, memoire, WS heartbeat toutes les 10s
17. `telegram_notifier.start()` — init httpx client, charge enabled depuis DB, pret a envoyer
18. `trailing_manager.start()` — smart OCO trailing : auto SL/TP sur nouvelles positions, trail a la hausse
19. `level_alert.start()` — s'abonne aux prix WS, detecte croisement niveaux cles, notif Telegram

L'arret se fait en ordre inverse. Chaque module expose `start()`/`stop()`.

### Pattern event-driven

`ws_manager` est un dispatcher central. Les modules s'abonnent aux events via `ws_manager.on(EVENT_TYPE, callback)`. Les types d'events : `execution_report`, `account_update`, `balance_update`, `list_status`, `price_update`, `kline_update`.

### Position tracking

Les positions n'existent pas nativement sur Binance spot/margin — elles sont reconstruites :
- Au demarrage : scan des balances spot/cross/isolated, detection des assets non-stablecoin avec solde > 0, calcul du prix d'entree moyen via l'historique des trades
- En continu : `_handle_execution_report` traite chaque fill pour ouvrir/DCA/reduire/fermer les positions
- Les positions actives sont gardees en memoire dans `_positions: dict[int, Position]` et persistees en DB
- Logique d'ambiguite margin : un BUY margin peut etre un close SHORT ou un open LONG (resolu via l'etat courant + dette)

### Frontend → Backend

- REST API : `routes/api_*.py` — CRUD positions, ordres, balances, trades, prix
- WebSocket : `routes/ws_dashboard.py` — broadcast `positions_snapshot` toutes les 2s + events prix/ordres/balances
- Frontend JS : modules IIFE (`WS`, `App`, `Positions`, `Trades`, `Balances`, `KlineChart`) communiquent via `WS.on(type, callback)`

## Conventions

- Langue du code : anglais. Langue de communication : francais
- Async partout cote backend (FastAPI + python-binance AsyncClient)
- Pas de commentaires superflus, pas de docstrings sauf API publiques
- Gestion d'erreurs uniquement aux frontieres (appels Binance, input utilisateur)
- Un seul utilisateur, pas de systeme d'auth

## Regles strictes

- **1 fichier = 1 responsabilite**, max ~1000 lignes. Au-dela de 800 lignes, se poser la question du decoupage. Verifier `wc -l` avant de grossir un fichier deja consequent
- **Decimal partout** : `from decimal import Decimal` pour tous les prix, quantites, PnL. Jamais de `float` pour de l'argent. Strings Binance → Decimal directement
- **Filtres Binance** : avant chaque ordre, valider via `symbol_filters.validate_order()` et arrondir avec `round_quantity()`/`round_price()`
- **UTC partout en DB** : conversion en local uniquement cote frontend JS
- **Crash recovery** : au redemarrage, reconstruire l'etat complet depuis Binance sans dupliquer en DB (upsert sur IDs Binance)

## API Binance — Points critiques

- **NE PAS se fier aux connaissances du modele** pour les endpoints Binance → consulter doc Binance en ligne
- Signature HMAC-SHA256 : percent-encoder les params AVANT de signer (changement 15/01/2026)
- User data stream : `POST /sapi/v1/userListenToken` (ancien listenKey retire 20/02/2026)
- OCO spot : `POST /api/v3/orderList/oco` (ancien endpoint deprecie)
- Margin : toujours specifier `sideEffectType` (AUTO_REPAY pour fermer, MARGIN_BUY pour ouvrir)
- Symboles WebSocket en minuscules (`btcusdc@ticker`, pas `BTCUSDC@ticker`)
- `executionReport` : utiliser `l` (last fill qty), PAS `z` (cumulative). Traiter PARTIALLY_FILLED comme FILLED

## Frontend

- **Mobile-first** : iPhone = usage principal. Touch-friendly (boutons min 44px), Tailwind breakpoints sm → md → lg
- **Tailwind CSS v3** : source `frontend/css/tailwind.css` → output `frontend/css/output.css`

## Git

- Commits en anglais, conventionnels : `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`
- Ne jamais commit `.env`, `data/*.db`, `__pycache__/`
- **Quand l'utilisateur dit "commit" ou "commit push" : TOUJOURS faire `git status` d'abord et inclure TOUS les fichiers modifies, sans exception.** Ne jamais oublier un fichier. Verifier apres le commit qu'il ne reste rien.

## Workflow — Regles de collaboration

- **TOUJOURS proposer la solution AVANT de coder.** Expliquer en 2-3 phrases ce qu'on va faire et attendre validation. Ne pas ecrire de code sans que l'utilisateur ait compris l'approche.
- **Quand un fix ne marche pas du premier coup** : s'arreter, analyser pourquoi, proposer une nouvelle approche au lieu de tenter des variations a l'aveugle.
- **Ne pas multiplier les tentatives** sur un meme probleme sans expliquer ce qui a change et pourquoi ca devrait marcher cette fois.
- **Frontend : toujours verifier la coherence HTML ↔ JS.** Si le JS utilise une valeur par defaut (ex: `_interval = '5m'`), verifier que le HTML a la classe `active` sur le bon bouton correspondant.
- **Quand on modifie un seuil ou une valeur par defaut** : chercher tous les endroits ou cette valeur est referencee (HTML, JS, backend) et les mettre a jour ensemble.

## Logging

- `structlog` avec JSON renderer. Niveaux : ERROR (erreurs Binance/reseau), WARNING (reconnexions), INFO (trades/ordres), DEBUG (prix)

## Indicateurs techniques — `kline_manager.compute_indicators()`

Tous les indicateurs sont calcules a la volee depuis les klines stockees en DB. Ils ne sont PAS stockes — le calcul sur 1000 bougies prend ~5-10ms. Ne pas dupliquer ces fonctions.

Appel : `GET /api/klines/{symbol}?indicators=ma,rsi,bb,...` — seuls les indicateurs demandes sont calcules.

| Cle API | Fonction | Description | Affiche sur chart |
|---------|----------|-------------|-------------------|
| `ma` | `_sma()` | Moving Average (SMA 7, 25, 99) | Oui (overlay main) |
| `ema` | `_ema()` | Exponential MA (EMA 7, 21, 50) | Non |
| `rsi` | `_rsi()` | Relative Strength Index (14, Wilder) | Oui (sub-chart) |
| `bb` | `_bollinger()` | Bollinger Bands (SMA 20 ± 2*std) | Oui (overlay main) |
| `obv` | `_obv()` | On-Balance Volume | Oui (sub-chart) |
| `macd` | `_macd()` | MACD (12, 26, 9) → line, signal, histogram | Oui (sub-chart) |
| `buy_sell` | `_buy_sell_pressure()` | Taker buy % - 50 (pression achat/vente) | Oui (sub-chart) |
| `stoch_rsi` | `_stoch_rsi()` | Stochastic RSI (14, 3, 3) → K, D | Non |
| `atr` | `_atr()` | Average True Range (14) | Non |
| `vwap` | `_vwap()` | Volume Weighted Average Price | Non |
| `adx` | `_adx()` | Average Directional Index (14) | Non |
| `mfi` | `_mfi()` | Money Flow Index (14) | Non |

Les indicateurs "Non" affiches sont prets a l'emploi pour une future page d'analyse technique / detection de signaux.

## File Map — Index complet du codebase

> **But** : eviter de re-scanner le projet a chaque conversation. Aller directement au bon fichier.
> **Regle** : lors de toute creation, suppression ou renommage de fichier, mettre a jour cette section automatiquement (sans que l'utilisateur ait a le demander).

### Backend entry point (`backend/`)

| Fichier | Responsabilite | Exports cles |
|---------|---------------|--------------|
| `main.py` | Entry point FastAPI, lifespan (init/shutdown ordre) | `app` |

### Core (`backend/core/`)

| Fichier | Responsabilite | Exports cles |
|---------|---------------|--------------|
| `config.py` | Settings Pydantic depuis `.env` | `settings` (singleton) |
| `database.py` | SQLAlchemy async engine, session, migrations | `engine`, `async_session`, `init_db()` |
| `models.py` | ORM : Position, Trade, Order, Balance, Price, Kline, Setting, OpportunityRecord, PriceAlert, LlmAnalysis, TradeSnapshot | 11 modeles declaratifs |

### Exchange (`backend/exchange/`)

| Fichier | Responsabilite | Exports cles |
|---------|---------------|--------------|
| `binance_client.py` | Wrapper AsyncClient Binance (spot+margin+OCO) | `_client` singleton, `place_order()`, `place_margin_order()`, `place_oco_order()`, `cancel_order()`, `cancel_margin_order()`, `cancel_oco_order()`, `cancel_margin_oco_order()`, `get_spot_balances()`, `get_cross/isolated_margin_balances()` |
| `ws_manager.py` | 3 streams WS (user data, prix, token refresh) + dispatcher events + kline stream + health tracking | `_manager` singleton, `on()`, `subscribe_symbol()`, `unsubscribe_symbol()`, `subscribe_kline()`, `unsubscribe_kline()`, `get_ws_health()` |
| `symbol_filters.py` | Cache LOT_SIZE/PRICE_FILTER/NOTIONAL, arrondi, validation | `init_filters()`, `round_quantity()`, `round_price()`, `validate_order()` |

### Trading (`backend/trading/`)

| Fichier | Responsabilite | Exports cles |
|---------|---------------|--------------|
| `pnl.py` | Calculs PnL centralises : brut, unrealized, realized, fees, win/loss. Fonctions pures Decimal, zero dependance ORM | `gross_pnl()`, `unrealized_pnl()`, `net_realized_pnl()`, `realized_pnl_pct()`, `total_fees()`, `estimated_exit_fees()`, `is_win()`, `ESTIMATED_EXIT_FEE_RATE` |
| `position_tracker.py` | State machine positions : handle fills, open/DCA/reduce/close. Delegue les ops Order DB a `order_manager` | `_positions` dict, `start()`, `stop()`, `get_positions()` |
| `position_reconciler.py` | Reconciliation : scan Binance au demarrage, backfill trades, verification order refs, reconciliation periodique 30min | `fast_load_from_db()`, `background_reconcile()`, `periodic_reconcile_loop()` |
| `order_manager.py` | Placement SL/TP/OCO, close position, cancel orders (individuels + OCO), cleanup stale orders, ensure order records | `place_stop_loss()`, `place_take_profit()`, `place_oco()`, `close_position()`, `cancel_position_orders()`, `cleanup_stale_orders()`, `ensure_oco_order_record()`, `ensure_order_record()`, `mark_order_status()`, `mark_oco_done()` |
| `trailing_manager.py` | Smart OCO trailing : auto-pose OCO sur nouvelles positions (niveaux cles), trail SL+TP a la hausse quand gain depasse seuil, respect override manuel | `start()`, `stop()`, `get_tracked()` |
| `trailing_levels.py` | Fonctions pures de calcul niveaux SL/TP : parse levels, find initial/trailing SL, next resistance, R:R, gain %, fallback | `gain_pct()`, `compute_rr()`, `find_initial_sl_tp()`, `find_trailing_sl_level()`, `find_next_resistance()`, `compute_initial_levels()` |
| `balance_tracker.py` | Snapshots balances spot/cross/isolated + conversion USD | `start()`, `stop()` |
| `price_recorder.py` | Enregistre prix ticker en DB periodiquement + cleanup | `start()`, `stop()` |

### Scoring (`backend/scoring/`)

| Fichier | Responsabilite | Exports cles |
|---------|---------------|--------------|
| `signal_engine.py` | Extraction signaux par timeframe + detection structure (rejets, tests niveaux, break-retest) sur 5m et 15m | `extract_signals()` |
| `scorer.py` | Scoring unifie par confluence a 6 couches scalp : 5m(30) + 15m(25) + 1h(15) + 4h(5) + flux(20) + macro(-5/+5) | `compute_unified_score()`, `TOTAL_MAX` |
| `timing_coach.py` | Evaluation timing entree : retest niveau, MACD 5m, RSI, spread, session Wall Street | `evaluate()` |

### Market (`backend/market/`)

| Fichier | Responsabilite | Exports cles |
|---------|---------------|--------------|
| `kline_manager.py` | Fetch klines Binance, stockage DB, calcul indicateurs, cleanup | `start()`, `stop()`, `fetch_and_store()`, `get_klines()`, `compute_indicators()` |
| `macro_tracker.py` | Fetch DXY, VIX, Nasdaq, Gold, US10Y, US5Y, Oil, USD/JPY via yfinance + spread 10Y-5Y calcule, cache memoire 5 min | `start()`, `stop()`, `get_macro_data()` |
| `whale_tracker.py` | Poll Binance aggTrades, detecte gros trades > seuil, deque 50 items | `start()`, `stop()`, `get_whale_alerts()` |
| `orderbook_tracker.py` | Poll Binance depth, calcul imbalance bid/ask, detection murs, spread, cache memoire | `start()`, `stop()`, `get_orderbook_data()`, `get_imbalance()` |
| `heatmap_manager.py` | Top 50 USDC par volume 24h, variation prix sur fenetre 4h glissante, cache memoire | `start()`, `stop()`, `get_heatmap_data()` |
| `market_analyzer.py` | Orchestrateur analyse : scoring 6 couches, niveaux cles (pivots, swings, Fibonacci, psycho), timing coach, alertes | `start()`, `stop()`, `get_analysis()`, `get_all_analyses()` |
| `analysis_formatter.py` | Descriptions textuelles signaux AT/macro, conversion signal→dict | `signal_to_dict()`, `format_qty()`, `TIMEFRAMES` |
| `llm_analyzer.py` | Appel Claude Opus 4.6 a la demande : collecte indicateurs multi-TF + niveaux + orderbook + whales + macro + news, construit prompt, parse JSON. Persiste analyses en DB, resout outcomes (TP/SL/expired) via klines, injecte track record dans le prompt pour auto-amelioration | `analyze()`, `get_last_analysis()`, `get_history()`, `get_stats()` |
| `opportunity_detector.py` | Filtre symboles sans position avec score unifie > seuil, messages FR, timing, cooldown | `start()`, `stop()`, `get_opportunities()` |

### Services (`backend/services/`)

| Fichier | Responsabilite | Exports cles |
|---------|---------------|--------------|
| `event_recorder.py` | Enregistre les raw WS events user data en JSONL rotatif (7j) + ring buffer memoire | `start()`, `stop()`, `record()`, `get_recent()` |
| `journal_snapshotter.py` | Capture snapshot contexte marche (AT, macro, orderbook, whales) a l'ouverture/DCA/fermeture de position | `capture_snapshot()` |
| `log_buffer.py` | Ring buffer structlog, capture processor, real-time subscribers | `capture_processor()`, `get_logs()`, `subscribe()`, `unsubscribe()` |
| `news_tracker.py` | Fetch RSS CoinDesk + Google News (crypto FR + macro FR), traduction EN→FR via deep-translator, cache memoire | `start()`, `stop()`, `get_news()` |
| `health_collector.py` | Aggrege la sante de tous les modules, DB stats, memoire, uptime | `start()`, `stop()`, `get_health()` |
| `telegram_notifier.py` | Envoi notifications Telegram via Bot API (httpx), toggle on/off persiste en DB, 3 categories (positions, ordres, niveaux) | `start()`, `stop()`, `notify()`, `is_enabled()`, `is_configured()`, `set_enabled()`, `test_connection()`, `notify_level_reached()` |
| `level_alert.py` | Detecte croisement prix/niveaux cles (pivots, supports, resistances, Fib, psycho) + alertes prix custom user, notif Telegram avec cooldown | `start()`, `stop()` |
| `opportunity_tracker.py` | Lifecycle opportunites detectees : record, resolve (TP/SL/expired), mark taken, stats | `start()`, `stop()`, `record_detection()`, `mark_taken()`, `get_history()`, `get_stats()` |

### Routes API (`backend/routes/`)

| Fichier | Endpoints | Responsabilite |
|---------|-----------|---------------|
| `position_helpers.py` | — | Helpers partages : `fetch_order_prices()` (query OCO+SL+TP, fallback stop_price→price), `pos_to_dict()` (serialisation position + order prices) |
| `dashboard.py` | `GET /` | Sert `index.html` |
| `ws_dashboard.py` | `WS /ws` | Broadcast positions (2s) + events temps reel. Utilise `position_helpers` pour serialisation |
| `api_positions.py` | `GET/POST/DELETE /api/positions/*` | CRUD positions, SL/TP/OCO/close. Utilise `position_helpers` pour serialisation |
| `api_orders.py` | `DELETE /api/orders/{id}` | Annuler un ordre |
| `api_balances.py` | `GET /api/balances`, `GET /api/balances/history` | Balances courantes + historique |
| `api_trades.py` | `GET /api/trades` | Historique trades (filtre symbol) |
| `api_prices.py` | `GET /api/prices/history`, `GET /api/prices/current` | Prix OHLC + prix courant |
| `api_portfolio.py` | `GET /api/portfolio/history` | Valeur portfolio agregeee dans le temps |
| `api_cycles.py` | `GET /api/cycles`, `GET /api/cycles/stats` | Cycles fermes/ouverts + stats (win rate, PnL) |
| `api_klines.py` | `GET /api/klines/symbols`, `GET /api/klines/{symbol}`, `GET /api/klines/{symbol}/trades`, `POST /api/klines/{symbol}/subscribe`, `POST /api/klines/{symbol}/unsubscribe` | Klines OHLCV + indicateurs + subscribe WS |
| `api_analysis.py` | `GET /api/analysis`, `GET /api/analysis/{symbol}` | Analyse du jour : biais, niveaux, macro, alertes |
| `api_heatmap.py` | `GET /api/heatmap?limit=50` | Heatmap crypto top 50 par volume, variation 4h |
| `api_orderbook.py` | `GET /api/orderbook`, `GET /api/orderbook/{symbol}` | Depth data : imbalance, murs, spread |
| `api_opportunities.py` | `GET /api/opportunities`, `GET /api/opportunities/history`, `GET /api/opportunities/stats` | Opportunites detectees + historique + stats win rate |
| `api_news.py` | `GET /api/news` | News RSS : CoinDesk + Google News |
| `api_journal.py` | `GET /api/journal/calendar`, `GET /api/journal/equity`, `GET /api/journal/entries` | Journal trading : calendrier PnL, equity curve + drawdowns, timeline trades avec snapshots |
| `api_health.py` | `GET /api/health`, `GET /api/health/logs`, `GET /api/health/events`, `GET /api/health/db` | Health systeme, logs recents, raw WS events, stats DB |
| `api_alerts.py` | `GET /api/alerts`, `POST /api/alerts`, `DELETE /api/alerts/{id}` | CRUD alertes prix custom (Telegram notif au croisement) |
| `api_llm.py` | `POST /api/llm/analyze`, `GET /api/llm/last`, `GET /api/llm/history`, `GET /api/llm/stats` | Analyse IA Claude Opus a la demande + historique + track record |
| `api_settings.py` | `GET /api/settings`, `GET /api/settings/{key}`, `PUT /api/settings/{key}`, `POST /api/settings/telegram/toggle`, `POST /api/settings/telegram/category`, `POST /api/settings/telegram/test` | CRUD settings DB + toggle/test/categories Telegram |

### Frontend (`frontend/`)

| Fichier | Responsabilite | Exports cles |
|---------|---------------|--------------|
| `index.html` | SPA : 9 tabs (cockpit/positions/cycles/trades/balances/chart/analyse/heatmap/health), modals SL/TP/OCO | — |
| `css/style.css` | Styles custom : couleurs PnL, cards, responsive, modals, chart indicators, heatmap tiles, analyse, health dots, log terminal, cockpit | Classes : `.pnl-positive/negative`, `.position-card`, `.cycle-card`, `.chart-interval-btn`, `.indicator-toggle`, `.subchart-label`, `.heatmap-tile`, `.bias-direction`, `.alert-card`, `.macro-card`, `.health-dot-*`, `.health-module-card`, `.log-terminal`, `.cockpit-card`, `.cockpit-position`, `.cockpit-bias-*` |
| `css/tailwind.css` | Source Tailwind (input pour compilation) | — |
| `css/output.css` | Tailwind compile (ne pas editer a la main) | — |
| `js/websocket.js` | Client WS avec reconnexion auto + dispatch par type | `WS.on(type, fn)` |
| `js/utils.js` | Helpers partages frontend : formatage prix, temps relatif | `Utils.timeAgo()`, `Utils.timeAgoShort()`, `Utils.fmtPrice()` |
| `js/app.js` | Orchestrateur : tabs, horloge, toasts, chargement initial | `App.toast()`, `App.switchTab()` |
| `js/positions.js` | Rendu cartes positions actives, tri, modals SL/TP/OCO/close, cancel orders avec toast contextuel | `Positions.load()`, `Positions.render()`, `Positions.showSL/TP/OCO()`, `Positions.confirmCancelOrders()` |
| `js/position-cards.js` | Construction HTML d'une carte position | `PositionCards.buildCardHtml()` |
| `js/trades.js` | Table historique trades | `Trades.load()`, `Trades.render()` |
| `js/cycles.js` | Cartes cycles fermes, PnL realise, pagination, filtres | `Cycles.load()`, `Cycles.render()` |
| `js/balances.js` | Table balances agregees par asset, total USD, chart portfolio | `Balances.load()`, `Balances.render()` |
| `js/charts.js` | Mini-charts prix (LightweightCharts), ligne entry price, chart portfolio. Filtre timestamps dupliques + valeurs invalides pour LightweightCharts | `Charts.createMiniChart()`, `Charts.appendPrice()`, `Charts.cleanup()`, `Charts.createPortfolioChart()`, `Charts.loadPortfolioData()` |
| `js/kline-chart.js` | Chart candlestick : klines, 7 indicateurs (MA/BB overlay + Vol/B-S/RSI/MACD/OBV sub-charts), markers fills, cycles overlay, crosshair sync, live WS | `KlineChart.init()`, `KlineChart.loadChart()` |
| `js/analysis.js` | Page analyse du jour : biais, niveaux cles, macro, alertes. Ecoute WS `analysis_update` | `Analysis.load()` |
| `js/heatmap.js` | Heatmap crypto : grille coloree de tiles par performance 4h | `Heatmap.load()` |
| `js/mini-trade-chart.js` | Moteur mini-charts candlestick avec entry/SL/TP, labels direction, badges timing | `MiniTradeChart.create()`, `.setData()`, `.updateLevels()`, `.appendCandle()`, `.addLabel()`, `.addTiming()`, `.destroy()`, `.fetchAndRender()` |
| `js/opportunities.js` | Mini-chart cards opportunites avec timing badges, dismiss session | `Opportunities.render()`, `Opportunities.update()`, `Opportunities.dismiss()` |
| `js/alerts.js` | Panel alertes prix custom sur page Chart : ajout/suppression, lignes sur chart | `Alerts.init()`, `Alerts.load()`, `Alerts.getAlerts()`, `Alerts.remove()` |
| `js/cockpit.js` | Cockpit unifie : portfolio, mini-charts positions, opportunites, track record, contexte (macro+whales repliable) | `Cockpit.load()` |
| `js/journal.js` | Page journal : equity curve + drawdowns, calendrier PnL heatmap (GitHub style), timeline trades avec contexte marche | `Journal.init()`, `Journal.load()` |
| `js/health.js` | Page health : statut modules, WS heartbeat, DB stats, terminal logs live | `Health.init()`, `Health.load()`, `Health.startPolling()`, `Health.stopPolling()` |

### Autres

| Fichier | Responsabilite |
|---------|---------------|
| `logo.svg` | Logo de l'app |
| `manifest.json` | PWA metadata (nom, icones, theme) |
| `requirements.txt` | Dependencies Python |
| `doc/private/PROJECT.md` | Documentation projet + reference API Binance |

## Fichiers locaux (non commites)

> **Regle** : au debut de chaque session, lire les fichiers listes ci-dessous pour avoir le contexte complet.
> Lors de toute modification pertinente, mettre a jour ces fichiers.

| Fichier | Contenu | Quand lire / modifier |
|---------|---------|----------------------|
| `.env` | API keys Binance, Telegram bot token, config DB | Lire : si besoin de verifier une variable d'env. Modifier : jamais (modifie par l'utilisateur) |
| `.claude/settings.local.json` | Permissions Claude Code projet (allow/deny) | Lire : pour savoir quelles commandes sont auto-approuvees. Modifier : si l'utilisateur demande d'ajouter/retirer des permissions ou rajouter automatiquement les commandes validées |
| `doc/private/VPS.md` | Commandes VPS, DB, scripts maintenance | Lire : quand on travaille sur le deploiement. Modifier : quand les commandes VPS changent |

# Architecture

┌─────────────────────────────────────────────────────────┐
│                        VPS (systemd)                     │
│                                                          │
│  ┌──────────────┐    WebSocket     ┌──────────────────┐  │
│  │   Binance    │◄───────────────►│                    │  │
│  │   API/WS     │                 │   Python Backend   │  │
│  └──────────────┘                 │   (FastAPI)        │  │
│                                   │                    │  │
│                                   │  - Position tracker│  │
│                                   │  - Order manager   │  │
│                                   │  - Price recorder  │  │
│                                   │  - Balance tracker │  │
│  ┌──────────────┐                 │                    │  │
│  │   SQLite DB  │◄───────────────►│                    │  │
│  │              │                 └────────┬───────────┘  │
│  │ - positions  │                          │              │
│  │ - trades     │                     HTTP + WS           │
│  │ - balances   │                          │              │
│  │ - prices     │                 ┌────────▼───────────┐  │
│  │ - orders     │                 │   Dashboard Web    │  │
│  └──────────────┘                 │   (HTML/JS/CSS)    │  │
│                                   └────────────────────┘  │
│                                            ▲              │
└────────────────────────────────────────────┼──────────────┘
                                             │
                                        Tailscale VPN
                                             │
                               ┌─────────────┼─────────────┐
                               │             │             │
                            PC/Mac       iPhone        Tablette
                           (navigateur)  (Safari)    (navigateur)

---

## Stack technique

| Composant | Technologie | Justification |
|-----------|-------------|---------------|
| **Backend** | Python 3.12+ / FastAPI | Async natif, WebSocket, léger |
| **Binance API** | python-binance 1.0.35+ | Meilleur support spot+margin, WebSocket user data stream à jour (post-deprecation listenKey) |
| **Base de données** | SQLite via SQLAlchemy + aiosqlite | Léger, pas de serveur DB, suffisant pour un utilisateur |
| **Frontend** | HTML/CSS/JS vanilla + Tailwind CSS (CDN) | Servi directement par FastAPI, pas de build step |
| **Temps réel** | WebSocket natif (backend↔frontend) + Binance WS streams | Mises à jour instantanées |
| **Déploiement** | systemd + GitHub | Pas de Docker, simple `git pull` + restart |
| **Accès distant** | Tailscale | VPN mesh, accès sécurisé depuis PC/iPhone |

---


