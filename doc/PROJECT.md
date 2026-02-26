# RootCoin - Trading Dashboard for Binance

## Vue d'ensemble

Application de trading avec dashboard web permettant de :
- Tracker en temps réel les positions ouvertes sur Binance (spot + margin cross/isolated)
- Afficher PnL, prix d'entrée, prix actuel, durée de la position
- Poser des ordres SL, TP, OCO, fermer ou annuler les ordres depuis le dashboard
- Stocker l'historique des trades, balances, et prix des tokens
- Tourner 24/7 sur un VPS accessible via Tailscale depuis n'importe quel appareil

---

## Stack technique

| Composant | Technologie | Justification |
|-----------|-------------|---------------|
| **Backend** | Python 3.11+ / FastAPI | Async natif, WebSocket, léger |
| **Binance API** | python-binance 1.0.35+ | Meilleur support spot+margin, WebSocket user data stream à jour (post-deprecation listenKey) |
| **Base de données** | SQLite via SQLAlchemy + aiosqlite | Léger, pas de serveur DB, suffisant pour un utilisateur |
| **Frontend** | HTML/CSS/JS vanilla + Tailwind CSS (CDN) | Servi directement par FastAPI, pas de build step |
| **Temps réel** | WebSocket natif (backend↔frontend) + Binance WS streams | Mises à jour instantanées |
| **Déploiement** | systemd + GitHub | Pas de Docker, simple `git pull` + restart |
| **Accès distant** | Tailscale | VPN mesh, accès sécurisé depuis PC/iPhone |

---

## Architecture

```
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
```

---

## Structure du projet

```
rootcoin/
├── PROJECT.md                  # Ce fichier
├── .env.example                # Template des variables d'environnement
├── .gitignore
├── requirements.txt
│
├── backend/
│   ├── __init__.py
│   ├── main.py                 # Point d'entrée FastAPI
│   ├── config.py               # Configuration (.env, constantes)
│   ├── database.py             # Setup SQLAlchemy + modèles
│   ├── models.py               # Modèles SQLAlchemy (tables DB)
│   │
│   ├── binance_client.py       # Wrapper python-binance (singleton)
│   ├── position_tracker.py     # Détection et suivi des positions
│   ├── order_manager.py        # Exécution SL/TP/OCO/Close/Cancel
│   ├── price_recorder.py       # Enregistrement périodique des prix
│   ├── balance_tracker.py      # Snapshot des balances
│   ├── ws_manager.py           # Gestion WebSocket Binance (user data + prix)
│   │
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── dashboard.py        # Route GET / → sert le HTML
│   │   ├── api_positions.py    # API REST positions
│   │   ├── api_orders.py       # API REST ordres (SL/TP/OCO/Close)
│   │   ├── api_balances.py     # API REST balances
│   │   ├── api_trades.py       # API REST historique trades
│   │   ├── api_prices.py       # API REST prix historiques (hours, order params)
│   │   ├── api_portfolio.py    # API REST historique portfolio (agrégation USD)
│   │   └── ws_dashboard.py     # WebSocket endpoint pour le frontend
│   │
│   └── utils/
│       ├── __init__.py
│       └── symbol_filters.py   # Cache exchangeInfo, validation/arrondi ordres
│
├── frontend/
│   ├── index.html              # Dashboard principal (SPA)
│   ├── css/
│   │   └── style.css           # Styles custom (Tailwind via CDN)
│   └── js/
│       ├── app.js              # Bootstrap, tabs, toasts
│       ├── websocket.js        # Connexion WebSocket au backend
│       ├── charts.js           # Mini charts positions + portfolio chart (Lightweight Charts)
│       ├── position-cards.js   # Construction et mise à jour DOM des cartes position
│       ├── positions.js        # Logique positions (DOM-diff, modals, ordres)
│       ├── trades.js           # Historique trades
│       └── balances.js         # Affichage balances + portfolio chart
│
├── data/
│   └── rootcoin.db             # Base SQLite (créée automatiquement)
│
└── scripts/
    ├── setup_vps.sh            # Script d'installation VPS
    ├── deploy.sh               # Pull + restart sur le VPS
    ├── backup_db.sh            # Script de backup de la DB
    └── rootcoin.service        # Fichier systemd
```

---

## Base de données - Schéma complet

### Table `positions`
Positions actuellement ouvertes (recalculées en temps réel).

| Colonne | Type | Description |
|---------|------|-------------|
| id | INTEGER PK | Auto-increment |
| symbol | TEXT NOT NULL | Ex: "BTCUSDT" |
| side | TEXT NOT NULL | "LONG" ou "SHORT" |
| entry_price | REAL NOT NULL | Prix moyen d'entrée |
| quantity | REAL NOT NULL | Quantité en base asset |
| market_type | TEXT NOT NULL | "SPOT", "CROSS_MARGIN", "ISOLATED_MARGIN" |
| current_price | REAL | Dernier prix connu |
| pnl_usd | REAL | PnL non réalisé en USD |
| pnl_pct | REAL | PnL en pourcentage |
| opened_at | DATETIME NOT NULL | Timestamp d'ouverture |
| updated_at | DATETIME | Dernière mise à jour |
| sl_order_id | TEXT | ID de l'ordre SL actif (si posé) |
| tp_order_id | TEXT | ID de l'ordre TP actif (si posé) |
| oco_order_list_id | TEXT | ID de l'OCO actif (si posé) |
| is_active | BOOLEAN DEFAULT 1 | Position encore ouverte |

### Table `trades`
Historique de tous les trades exécutés (rempli via user data stream).

| Colonne | Type | Description |
|---------|------|-------------|
| id | INTEGER PK | Auto-increment |
| binance_trade_id | TEXT UNIQUE | ID du trade côté Binance |
| binance_order_id | TEXT | ID de l'ordre associé |
| symbol | TEXT NOT NULL | Ex: "BTCUSDT" |
| side | TEXT NOT NULL | "BUY" ou "SELL" |
| price | REAL NOT NULL | Prix d'exécution |
| quantity | REAL NOT NULL | Quantité exécutée |
| quote_qty | REAL | Montant en quote asset |
| commission | REAL | Frais payés |
| commission_asset | TEXT | Asset des frais (BNB, USDT...) |
| market_type | TEXT NOT NULL | "SPOT", "CROSS_MARGIN", "ISOLATED_MARGIN" |
| is_maker | BOOLEAN | Maker ou taker |
| realized_pnl | REAL | PnL réalisé (calculé) |
| executed_at | DATETIME NOT NULL | Timestamp d'exécution |
| created_at | DATETIME DEFAULT CURRENT_TIMESTAMP | |

### Table `orders`
Tous les ordres passés ou en cours (SL, TP, OCO, etc.).

| Colonne | Type | Description |
|---------|------|-------------|
| id | INTEGER PK | Auto-increment |
| binance_order_id | TEXT UNIQUE | ID Binance |
| binance_order_list_id | TEXT | ID de la liste (OCO) |
| symbol | TEXT NOT NULL | |
| side | TEXT NOT NULL | "BUY" ou "SELL" |
| type | TEXT NOT NULL | "LIMIT", "MARKET", "STOP_LOSS_LIMIT", "TAKE_PROFIT_LIMIT", "OCO" |
| status | TEXT NOT NULL | "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED" |
| price | REAL | Prix limite |
| stop_price | REAL | Prix de déclenchement |
| quantity | REAL NOT NULL | |
| filled_qty | REAL DEFAULT 0 | Quantité remplie |
| market_type | TEXT NOT NULL | "SPOT", "CROSS_MARGIN", "ISOLATED_MARGIN" |
| purpose | TEXT | "SL", "TP", "OCO_SL", "OCO_TP", "CLOSE", "MANUAL" |
| position_id | INTEGER FK | Lien vers la position |
| created_at | DATETIME DEFAULT CURRENT_TIMESTAMP | |
| updated_at | DATETIME | |

### Table `balances`
Snapshots périodiques des balances (toutes les 5 min + à chaque changement).

| Colonne | Type | Description |
|---------|------|-------------|
| id | INTEGER PK | Auto-increment |
| asset | TEXT NOT NULL | "BTC", "ETH", "USDT"... |
| free | REAL NOT NULL | Solde disponible |
| locked | REAL NOT NULL | Solde verrouillé (en ordres) |
| borrowed | REAL DEFAULT 0 | Emprunté (margin) |
| interest | REAL DEFAULT 0 | Intérêts accumulés (margin) |
| net | REAL NOT NULL | Net = free + locked - borrowed - interest |
| wallet_type | TEXT NOT NULL | "SPOT", "CROSS_MARGIN", "ISOLATED_MARGIN" |
| usd_value | REAL | Valeur en USD au moment du snapshot |
| snapshot_at | DATETIME NOT NULL | Timestamp du snapshot |

### Table `prices`
Historique des prix des tokens suivis.

| Colonne | Type | Description |
|---------|------|-------------|
| id | INTEGER PK | Auto-increment |
| symbol | TEXT NOT NULL | "BTCUSDT", "ETHUSDT"... |
| price | REAL NOT NULL | Prix |
| source | TEXT DEFAULT 'ticker' | "ticker", "kline", "trade" |
| recorded_at | DATETIME NOT NULL | Timestamp |

**Index** : `(symbol, recorded_at)` pour les requêtes de graphiques.

### Table `settings`
Configuration persistante de l'application.

| Colonne | Type | Description |
|---------|------|-------------|
| key | TEXT PK | Clé de config |
| value | TEXT | Valeur (JSON sérialisé si complexe) |
| updated_at | DATETIME | |

---

## Référence API Binance (endpoints utilisés)

### Base URLs
- REST API : `https://api.binance.com`
- WebSocket : `wss://stream.binance.com:9443`
- User data stream (nouveau) : via `POST /sapi/v1/userListenToken` + WebSocket subscribe

### Authentification
- Header : `X-MBX-APIKEY: <api_key>`
- Signature HMAC-SHA256 sur les paramètres
- **IMPORTANT (depuis 2026-01-15)** : percent-encoder les payloads AVANT de calculer la signature

### Spot

| Action | Méthode | Endpoint |
|--------|---------|----------|
| Passer un ordre | POST | `/api/v3/order` |
| Annuler un ordre | DELETE | `/api/v3/order` |
| Annuler tous les ordres | DELETE | `/api/v3/openOrders` |
| Statut d'un ordre | GET | `/api/v3/order` |
| Ordres ouverts | GET | `/api/v3/openOrders` |
| Tous les ordres | GET | `/api/v3/allOrders` |
| Info compte (balances) | GET | `/api/v3/account` |
| Historique trades | GET | `/api/v3/myTrades` |
| OCO | POST | `/api/v3/orderList/oco` |
| Annuler OCO | DELETE | `/api/v3/orderList` |
| Ordres OCO ouverts | GET | `/api/v3/openOrderList` |

**Paramètres communs pour les ordres :**
- `symbol` (STRING) - Ex: "BTCUSDT"
- `side` (ENUM) - BUY, SELL
- `type` (ENUM) - LIMIT, MARKET, STOP_LOSS_LIMIT, TAKE_PROFIT_LIMIT, LIMIT_MAKER
- `timeInForce` (ENUM) - GTC, IOC, FOK
- `quantity` (DECIMAL)
- `price` (DECIMAL)
- `stopPrice` (DECIMAL) - Pour STOP_LOSS/TAKE_PROFIT
- `timestamp` (LONG) - Requis pour tous les endpoints signés

**Paramètres OCO (`POST /api/v3/orderList/oco`) :**
- `symbol`, `side`, `quantity` - Obligatoires
- `aboveType` - LIMIT_MAKER, TAKE_PROFIT, TAKE_PROFIT_LIMIT
- `abovePrice` - Prix du take profit
- `belowType` - STOP_LOSS, STOP_LOSS_LIMIT
- `belowPrice` / `belowStopPrice` - Prix du stop loss

### Margin

| Action | Méthode | Endpoint |
|--------|---------|----------|
| Passer un ordre margin | POST | `/sapi/v1/margin/order` |
| Annuler un ordre margin | DELETE | `/sapi/v1/margin/order` |
| Annuler tous ordres margin | DELETE | `/sapi/v1/margin/openOrders` |
| Emprunter / Rembourser | POST | `/sapi/v1/margin/borrow-repay` |
| Info compte cross margin | GET | `/sapi/v1/margin/account` |
| Info compte isolated margin | GET | `/sapi/v1/margin/isolated/account` |
| Ordres margin ouverts | GET | `/sapi/v1/margin/openOrders` |
| Historique trades margin | GET | `/sapi/v1/margin/myTrades` |
| OCO margin | POST | `/sapi/v1/margin/order/oco` |
| Max empruntable | GET | `/sapi/v1/margin/maxBorrowable` |

**Paramètres spécifiques margin :**
- `isIsolated` (STRING) - "TRUE" pour isolated, omis ou "FALSE" pour cross
- `sideEffectType` (ENUM) :
  - `NO_SIDE_EFFECT` - Pas d'emprunt auto
  - `MARGIN_BUY` - Emprunt auto
  - `AUTO_REPAY` - Remboursement auto
  - `AUTO_BORROW_REPAY` - Emprunt + remboursement auto
- `autoRepayAtCancel` (BOOLEAN) - Rembourser si l'ordre est annulé (défaut: true)

### WebSocket Streams

| Stream | URL |
|--------|-----|
| Ticker prix | `<symbol>@ticker` (ex: `btcusdt@ticker`) |
| Mini ticker | `<symbol>@miniTicker` |
| Kline | `<symbol>@kline_<interval>` (intervals: 1m, 5m, 15m, 1h, 4h, 1d...) |
| Book ticker | `<symbol>@bookTicker` |
| All mini tickers | `!miniTicker@arr` |

**User Data Stream (nouveau système post 20/02/2026) :**
- Obtenir un listen token : `POST /sapi/v1/userListenToken`
- S'abonner via WebSocket : `userDataStream.subscribe` avec le `listenToken`
- Événements reçus :
  - `outboundAccountPosition` → changements de balance
  - `executionReport` → mises à jour d'ordres (NEW, FILLED, CANCELED...)
  - `balanceUpdate` → dépôts, retraits, transferts
  - `listStatus` → statut des listes d'ordres (OCO)

**Note** : les symboles dans les streams doivent être en minuscules (`btcusdt`, pas `BTCUSDT`).

### Rate Limits
- 6,000 poids / minute (par IP)
- 50 ordres / 10 secondes (par compte)
- 160,000 ordres / jour (par compte)
- WebSocket : 5 messages/sec, max 1024 streams/connexion, 300 connexions/5min

---

## Phases d'implémentation

### Phase 1 : Fondations (Backend Core) --- DONE
**Objectif** : Backend fonctionnel qui se connecte à Binance et stocke les données.

**Testé le 20/02/2026** : serveur lancé avec succès sur le port 8001, connexion Binance OK.
```
INFO:     Uvicorn running on http://0.0.0.0:8001 (Press CTRL+C to quit)
{"event": "rootcoin_starting", "level": "info", "timestamp": "2026-02-20T17:02:27.765621Z"}
{"event": "binance_client_initialized", "level": "info", "timestamp": "2026-02-20T17:02:28.809652Z"}
{"event": "rootcoin_started", "level": "info", "timestamp": "2026-02-20T17:02:28.809652Z"}
INFO:     Application startup complete.
```

1. **Setup projet** --- DONE
   - Initialiser le repo git
   - Créer `requirements.txt` avec les dépendances
   - Créer `.env.example` et `.gitignore`
   - Créer `backend/config.py` (chargement .env)

2. **Base de données** --- DONE
   - Créer `backend/database.py` (engine SQLAlchemy async + session)
   - Créer `backend/models.py` (tous les modèles : positions, trades, orders, balances, prices, settings)
   - Migration automatique au démarrage (create_all)

3. **Client Binance** --- DONE
   - Créer `backend/binance_client.py`
   - Singleton AsyncClient python-binance
   - Fonctions wrapper : get_spot_balances, get_margin_balances, get_open_orders, place_order, cancel_order
   - Gestion des erreurs API

4. **Point d'entrée FastAPI** --- DONE
   - Créer `backend/main.py`
   - Startup : init DB, init client Binance, démarrer les services background
   - Shutdown : cleanup propre
   - Servir les fichiers statiques du frontend

### Phase 2 : Tracking en temps réel --- DONE
**Objectif** : Détecter et suivre automatiquement les positions ouvertes.

**Testé le 20/02/2026** : tous les modules démarrent, streams connectés, prix enregistrés, balances snapshotées.
```
{"symbols": ["BTCUSDC", "BNBUSDC", "ETHUSDC"], "event": "ws_manager_started", "level": "info", "timestamp": "2026-02-20T17:49:12.227488Z"}
{"event": "listen_token_obtained", "level": "info", "timestamp": "2026-02-20T17:49:12.855468Z"}
{"count": 0, "event": "position_scan_complete", "level": "info", "timestamp": "2026-02-20T17:49:13.259527Z"}
{"event": "position_tracker_started", "level": "info", "timestamp": "2026-02-20T17:49:13.259527Z"}
{"event": "price_recorder_started", "level": "info", "timestamp": "2026-02-20T17:49:13.259527Z"}
{"event": "balance_tracker_started", "level": "info", "timestamp": "2026-02-20T17:49:13.259527Z"}
{"event": "rootcoin_started", "level": "info", "timestamp": "2026-02-20T17:49:13.259527Z"}
{"symbols": ["BTCUSDC", "BNBUSDC", "ETHUSDC"], "event": "price_stream_connected", "level": "info", "timestamp": "2026-02-20T17:49:13.505380Z"}
{"event": "user_data_stream_connected", "level": "info", "timestamp": "2026-02-20T17:49:13.798794Z"}
{"count": 2, "event": "balance_snapshot_complete", "level": "info", "timestamp": "2026-02-20T17:49:14.025450Z"}
```

5. **Position Tracker** --- DONE
   - Créer `backend/position_tracker.py`
   - Au démarrage : scanner les balances spot/margin pour détecter les positions existantes
   - Calculer le prix d'entrée moyen depuis l'historique des trades récents
   - Écouter le user data stream pour les nouveaux trades
   - Mettre à jour les positions en DB en temps réel
   - Détecter la fermeture d'une position (quantité → 0)

6. **WebSocket Manager Binance** --- DONE
   - Créer `backend/ws_manager.py`
   - User data stream (spot + margin) via le nouveau listenToken
   - Price ticker streams pour les symboles des positions ouvertes
   - Reconnexion automatique en cas de déconnexion
   - Dispatch des événements vers les autres modules

7. **Price Recorder** --- DONE
   - Créer `backend/price_recorder.py`
   - Enregistrer les prix toutes les minutes pour les tokens avec position ouverte
   - Enregistrer les prix toutes les 5 minutes pour les tokens suivis (watchlist)
   - Nettoyage automatique des vieux prix (garder 30 jours de données minute, puis agrégé en horaire)

8. **Balance Tracker** --- DONE
   - Créer `backend/balance_tracker.py`
   - Snapshot toutes les 5 minutes
   - Snapshot immédiat sur événement `outboundAccountPosition`
   - Calculer la valeur USD de chaque asset

### Phase 3 : Gestion des ordres --- DONE
**Objectif** : Pouvoir poser SL/TP/OCO et fermer des positions depuis l'API.

**Testé le 20/02/2026** : toutes les routes API opérationnelles, filtres Binance chargés (3501 paires).
```
{"count": 3501, "event": "symbol_filters_initialized"}
GET /api/positions → []
GET /api/balances → [{"asset": "BNB", ...}, {"asset": "USDC", "net": "9656.15", ...}]
GET /api/prices/BTCUSDC → [{"price": "67599.98", ...}, ...]
```

9. **Order Manager** --- DONE
   - Créer `backend/order_manager.py`
   - `place_stop_loss(position_id, price)` → STOP_LOSS_LIMIT
   - `place_take_profit(position_id, price)` → TAKE_PROFIT_LIMIT
   - `place_oco(position_id, tp_price, sl_price)` → OCO via `/api/v3/orderList/oco` (spot) ou `/sapi/v1/margin/order/oco` (margin)
   - `close_position(position_id)` → MARKET order pour fermer, avec AUTO_REPAY si margin
   - `cancel_order(order_id)` → annuler un SL/TP/OCO existant
   - `modify_sl_tp(position_id, new_sl, new_tp)` → cancel + replace
   - Toutes les fonctions gèrent le `sideEffectType` automatiquement selon le type de position
   - `backend/utils/symbol_filters.py` : cache exchangeInfo, round_quantity/price, validate_order

10. **Routes API REST** --- DONE
    - `GET /api/positions` → liste des positions actives
    - `GET /api/positions/{id}` → détail d'une position
    - `POST /api/positions/{id}/sl` → poser un stop loss `{price}`
    - `POST /api/positions/{id}/tp` → poser un take profit `{price}`
    - `POST /api/positions/{id}/oco` → poser un OCO `{tp_price, sl_price}`
    - `POST /api/positions/{id}/close` → fermer immédiatement
    - `DELETE /api/orders/{id}` → annuler un ordre
    - `GET /api/balances` → balances actuelles
    - `GET /api/balances/history` → historique des balances
    - `GET /api/trades` → historique des trades
    - `GET /api/trades?symbol=BTCUSDT` → trades filtrés
    - `GET /api/prices/{symbol}` → prix historiques
    - `GET /api/prices/{symbol}/current` → prix actuel

### Phase 4 : Dashboard Frontend --- DONE
**Objectif** : Interface web complète et responsive.

**Testé le 20/02/2026** : dashboard accessible, WS connecté (dot vert), onglets fonctionnels, balances affichées.

11. **Layout principal** --- DONE
    - `frontend/index.html` - Structure HTML
    - Header : logo, statut connexion Binance, heure
    - Sidebar ou tabs : Positions, Trades, Balances, Settings
    - Zone principale : contenu dynamique

12. **Vue Positions (vue principale)** --- DONE
    - Tableau des positions ouvertes :
      - Symbole, Side (LONG/SHORT avec couleur), Type (Spot/Margin)
      - Prix d'entrée, Prix actuel (temps réel)
      - Quantité, Valeur USD
      - PnL ($ et %, coloré vert/rouge)
      - Durée (depuis ouverture)
      - SL/TP actifs (affichés si posés)
    - Actions par position :
      - Bouton "SL" → modal pour entrer le prix
      - Bouton "TP" → modal pour entrer le prix
      - Bouton "OCO" → modal pour SL + TP
      - Bouton "Close" → confirmation puis fermeture market
      - Bouton "Cancel SL/TP" si un ordre est actif
    - Mise à jour en temps réel via WebSocket (pas de polling)

13. **Vue Trades** --- DONE
    - Tableau historique des trades
    - Filtres : symbole, date, side
    - PnL réalisé par trade
    - Totaux et résumé

14. **Vue Balances** --- DONE
    - Balances actuelles par wallet (Spot, Cross Margin, Isolated)
    - Graphique d'évolution de la valeur totale du portefeuille
    - Détail par asset

15. **WebSocket Frontend** --- DONE
    - `frontend/js/websocket.js`
    - Connexion WebSocket au backend (`ws://host/ws`)
    - Réception des events : position_update, price_update, order_update, balance_update
    - Mise à jour du DOM en temps réel
    - Reconnexion automatique avec backoff exponentiel

16. **Graphiques** --- DONE
    - TradingView Lightweight Charts v4 (CDN `unpkg.com/lightweight-charts@4.2.2`)
    - Mini chart area (120px) par position : historique 24h + mise à jour temps réel (1 point/min)
    - Chart d'évolution du portfolio dans la vue Balances (area 200px, sélecteur 24h/7d/30d)
    - DOM-diff sur les cartes positions pour préserver les charts lors des re-renders toutes les 2s
    - Backend : `GET /api/portfolio/history` (agrégation `usd_value` par snapshot), `GET /api/prices/{symbol}?hours=&order=asc`
    - `balance_tracker` enrichi : calcul automatique de `usd_value` sur chaque snapshot
    - Header responsive : 2 lignes sur mobile (logo+clock / tabs pleine largeur), 1 ligne sur desktop

### Phase 5 : Déploiement VPS --- DONE
**Objectif** : Faire tourner le système 24/7 sur un VPS accessible de partout.

**Déployé le 20/02/2026** : VPS Hetzner, Ubuntu 24.04, Tailscale sécurisé.
```
Dashboard accessible via http://<tailscale-ip>:8001 (Tailscale uniquement)
IP publique bloquée par ufw, accès SSH + Tailscale only
Service systemd actif, auto-restart on crash
```

17. **GitHub repo** --- DONE
    - Repo privé sur GitHub
    - `.gitignore` : `.env`, `data/*.db`, `__pycache__/`, `*.pyc`
    - Deploy key SSH configurée sur le VPS
    - Workflow : dev local → `git push` → `deploy-vps.bat` (un clic)

18. **Setup VPS** --- DONE
    - VPS (2 vCPU, 4 Go RAM, 80 Go SSD)
    - `scripts/setup_vps.sh` — installation automatisée
    - Python 3.11+, venv, dépendances installées
    - Tailscale installé et connecté
    - Firewall ufw : SSH only, port 8001 bloqué en public (accessible via Tailscale)
    - Code cloné dans `/home/rootcoin_app`

19. **Service systemd** --- DONE
    - Fichier `scripts/rootcoin.service`
    - `Restart=always` → redémarre auto si crash
    - `RestartSec=5` → attend 5s avant de relancer
    - Logs via journalctl (`journalctl -u rootcoin -f`)
    - Démarrage automatique au boot du VPS

    ```ini
    [Unit]
    Description=RootCoin Trading Dashboard
    After=network-online.target
    Wants=network-online.target

    [Service]
    Type=simple
    User=root
    WorkingDirectory=/home/rootcoin_app
    ExecStart=/home/rootcoin_app/venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8001
    Restart=always
    RestartSec=5
    EnvironmentFile=/home/rootcoin_app/.env

    [Install]
    WantedBy=multi-user.target
    ```

20. **Accès distant** --- DONE
    - Tailscale installé sur VPS, PC et iPhone
    - Dashboard accessible via `http://<tailscale-ip>:8001` (Tailscale)
    - IP publique bloquée par firewall
    - Accès depuis iPhone via Tailscale app + Safari (ajouté sur l'écran d'accueil)
    - Accès depuis PC via Chrome (installé comme app dans la barre des tâches)

21. **Workflow de mise à jour** --- DONE
    - `deploy-vps.bat` à la racine du projet : deploy en un double-clic depuis le PC
    - Ou manuellement : `ssh root@<tailscale-ip> "bash /home/rootcoin_app/scripts/deploy.sh"`
    - Script `scripts/deploy.sh` : git pull → update deps → rebuild Tailwind → restart service

22. **Monitoring et maintenance** --- DONE
    - systemd gère le restart auto
    - Logs via `journalctl -u rootcoin -f` (structurés JSON)
    - Script de backup DB (`scripts/backup_db.sh`) — rétention 30 jours
    - Notifications Telegram en cas d'erreur critique (à configurer)

---

## Détails d'implémentation clés

### Détection des positions

Le concept de "position" n'existe pas nativement en spot/margin sur Binance (contrairement aux futures). On doit le reconstruire :

1. **Au démarrage** :
   - Récupérer les balances spot (`GET /api/v3/account`)
   - Récupérer les balances cross margin (`GET /sapi/v1/margin/account`)
   - Récupérer les balances isolated margin (`GET /sapi/v1/margin/isolated/account`)
   - Pour chaque asset avec balance > 0 (hors USDT/BUSD/stablecoins) → position LONG potentielle
   - Pour chaque asset avec `borrowed > 0` en margin → position SHORT potentielle
   - Calculer le prix d'entrée moyen via `GET /api/v3/myTrades` ou `/sapi/v1/margin/myTrades`

2. **En continu** :
   - Écouter `executionReport` du user data stream
   - Sur chaque trade FILLED → mettre à jour la position correspondante
   - Si balance d'un asset passe à 0 → marquer la position comme fermée
   - Si nouveau trade sur un asset sans position → créer une nouvelle position

### Cas edge critiques (retour d'expérience ancien système)

Ces cas ont été rencontrés en production sur un précédent logiciel de trading. Ils DOIVENT être gérés dans le position_tracker (Phase 2) et l'order_manager (Phase 3).

#### 1. Ambiguïté MARGIN BUY : close SHORT ou open LONG ?
Un `BUY` sur le compte margin peut signifier :
- **Fermeture d'un SHORT** (rachat pour rembourser l'emprunt)
- **Ouverture d'un LONG MARGIN** (achat avec emprunt USDC)

**Solution** : vérifier la dette margin (`borrowed > 0` sur le base asset) pour trancher. Si dette > 0, c'est un close SHORT. Sinon, c'est un open LONG.

#### 2. Ambiguïté MARGIN SELL : close LONG ou open SHORT ?
Un `SELL` sur le compte margin peut signifier :
- **Fermeture d'un LONG MARGIN** (vente de la position)
- **Ouverture d'un SHORT** (emprunt + vente)

**Solution** : vérifier s'il existe une position LONG MARGIN ouverte pour ce symbole. Si oui, c'est un close LONG. Sinon, c'est un open SHORT.

#### 3. DCA (Dollar Cost Averaging)
Plusieurs achats successifs sur le même token = une seule position, pas plusieurs.
- Recalculer le **prix moyen pondéré** : `avg_price = (qty1*price1 + qty2*price2) / (qty1+qty2)`
- Additionner les quantités

#### 4. Sorties partielles (scaling out)
Vente d'une partie seulement de la position :
- Réduire `quantity` de la position, ne PAS la fermer
- Calculer le PnL sur la quantité vendue uniquement
- La position reste ouverte avec le reliquat

#### 5. Dust detection
Après une vente, un résidu minuscule peut rester (à cause des arrondis, frais, etc.) :
- Si résidu < 5 USDC OU < 1% de la position originale → considérer comme fermée
- Ne pas créer de "mini-position" avec le dust

#### 6. Ordres exécutés en tranches (partial fills)
Un seul ordre peut générer plusieurs trades (fills) avec le même `orderId` :
- Chaque fill = un `Trade` en DB (avec `binance_trade_id` unique)
- La `Position` agrège toutes les quantités

#### 7. Gestion des frais (fees) — POINT CRITIQUE
**Erreur récurrente de l'ancien système : les fees étaient souvent oubliées.** Toujours y penser à chaque étape.

**Sources des fees :**
- Chaque trade Binance a un champ `commission` + `commission_asset` (dans `executionReport`)
- Les fees peuvent être en quote asset (USDC), en base asset (BTC), ou en BNB (si discount activé)

**Fees en base asset (le piège principal) :**
- Achat 1.0 BTC avec fee 0.001 BTC → on possède réellement 0.999 BTC
- La quantité de la position DOIT être ajustée : `qty_réelle = qty_achetée - fee`
- Si on ne le fait pas → quand on essaie de vendre toute la position, l'ordre est rejeté (solde insuffisant)

**Fees à stocker systématiquement :**
- Sur chaque `Trade` : `commission` et `commission_asset` (toujours remplis, jamais ignorés)
- Priorité 1 : fees depuis le user data stream (`executionReport` contient commission directement)
- Priorité 2 : fallback via `GET /api/v3/myTrades` si le stream a raté le trade

**Fees et PnL :**
- Le PnL affiché en temps réel = PnL brut (sans fees), pour la simplicité
- Le PnL final (position fermée) devrait idéalement inclure les fees d'entrée + sortie
- Les fees s'accumulent sur plusieurs fills si l'ordre est exécuté en tranches

#### 8. Auto-repay dette margin
Quand un trade manuel est fait depuis l'app Binance (pas via notre système) :
- Le repay n'est PAS automatique (contrairement à `sideEffectType=AUTO_REPAY`)
- Après détection d'un close SHORT/LONG margin, vérifier la dette résiduelle
- Si dette > 0 et solde disponible, rembourser automatiquement
- **Retry nécessaire** : les fills arrivent en décalé, le solde n'est pas immédiatement disponible (3 tentatives, 2s d'intervalle)

#### 9. Résidus post-close SHORT (faux LONG)
Après fermeture d'un SHORT, des micro-BUY margin peuvent arriver :
- Remboursement d'intérêts accumulés
- Arrondi du rachat (ceil au step_size)
- Fills décalés du même ordre

**Solution** : si un MARGIN BUY arrive < 5 min après un close SHORT sur le même symbole, vérifier la dette avant de créer un LONG. Si pas de dette → ignorer (résidu). Seuil minimum de 50 USDC pour créer un LONG MARGIN.

#### 10. Intérêts margin dans le PnL
Les intérêts margin s'accumulent pendant la durée de la position :
- **SHORT** : intérêts sur le base asset emprunté (ex: BTC), convertir en USDC au prix de fermeture
- **LONG MARGIN** : intérêts sur USDC emprunté
- PnL net = PnL brut - intérêts en USDC
- Récupérer les intérêts via `get_margin_account()` au moment du close

#### 11. Vente du résidu post-close SHORT
Après rachat + repay d'un SHORT, du base asset résiduel peut traîner en compte margin :
- Si valeur > min_notional (~10 USDC) → revendre automatiquement en USDC
- Si valeur < min_notional → laisser (dust non vendable)

#### 12. User Data Stream — Pièges rencontrés (retour d'expérience)

**DEUX streams séparés obligatoires (ancien système pré-20/02/2026) :**
- Un trade margin n'apparaît PAS dans le stream SPOT et vice-versa
- Les deux doivent tourner en parallèle

**NOUVEAU SYSTÈME (post 20/02/2026) :**
- Les anciens endpoints `POST/PUT/DELETE /api/v3/userDataStream` et `/sapi/v1/userDataStream` sont **RETIRÉS**
- Nouveau endpoint unique : `POST /sapi/v1/userListenToken` → retourne un `listenToken`
- S'abonner via WebSocket avec `userDataStream.subscribe` + le `listenToken`
- Voir la section "WebSocket Streams" de ce document pour les détails

**Listen token : refresh obligatoire :**
- Le listen token expire sans refresh régulier
- Rafraîchir périodiquement (toutes les 30 min par sécurité)
- Si le refresh échoue → recréer un nouveau token et reconnecter le WS
- Au shutdown : fermer proprement pour ne pas laisser des streams orphelins

**executionReport — Champs critiques à ne pas confondre :**
- `l` = last executed quantity (la quantité de CE fill) — **UTILISER CELUI-CI**
- `z` = cumulative filled quantity (total cumulé de tous les fills de l'ordre)
- `L` = last executed price (prix de CE fill)
- `n` = commission amount (fee de CE fill)
- `N` = commission asset (USDC, BTC, BNB...)
- Erreur fréquente : utiliser `z` au lieu de `l` → quantité doublée/triplée sur les partial fills

**Traiter FILLED et PARTIALLY_FILLED :**
- Ne pas attendre que l'ordre soit complètement FILLED pour agir
- Chaque PARTIALLY_FILLED est un fill réel avec sa propre quantité (`l`) et son propre fee (`n`)
- Un ordre LIMIT peut générer 1 à N fills avant d'être FILLED

**Classification des ordres (distinguer nos ordres des trades manuels) :**
- Le `clientOrderId` (`c`) permet de distinguer l'origine
- Nos ordres : préfixer avec un identifiant unique (ex: `rootcoin_`)
- Ordres manuels depuis l'app Binance : préfixes `web_`, `android_`, `ios_`
- Ordres depuis TradingView/GoodCrypto : préfixes spécifiques
- **Règle de sécurité** : si l'ordre n'est pas reconnu comme interne → le traiter comme manuel (mieux vaut tracker un trade en trop que d'en rater un)

**position_side pour les trades margin — attention au piège :**
- MARGIN + SELL = potentiellement SHORT (mais peut aussi être close LONG)
- MARGIN + BUY = potentiellement close SHORT (mais peut aussi être open LONG)
- Le stream ne donne PAS l'intention → c'est le position_tracker qui doit résoudre l'ambiguïté en croisant avec l'état des positions et la dette Binance

---

### Calcul du PnL

**Position LONG (spot/margin) :**
```
PnL brut = (current_price - entry_price) * quantity
PnL% = ((current_price / entry_price) - 1) * 100
```

**Position SHORT (margin) :**
```
PnL brut = (entry_price - current_price) * quantity
PnL% = ((entry_price / current_price) - 1) * 100
```

**PnL net (margin uniquement) :**
```
PnL net = PnL brut - intérêts margin (en USDC)
```

Note : les frais de trading ne sont pas inclus dans le PnL affiché (amélioration future).

### Gestion des ordres SL/TP/OCO

**Stop Loss (spot) :**
```python
client.create_order(
    symbol='BTCUSDT',
    side='SELL',           # SELL pour fermer un LONG
    type='STOP_LOSS_LIMIT',
    quantity=position.quantity,
    price=sl_price * 0.999,  # Légèrement en dessous du stop pour assurer l'exécution
    stopPrice=sl_price,
    timeInForce='GTC'
)
```

**Take Profit (spot) :**
```python
client.create_order(
    symbol='BTCUSDT',
    side='SELL',
    type='TAKE_PROFIT_LIMIT',
    quantity=position.quantity,
    price=tp_price * 1.001,  # Légèrement au dessus
    stopPrice=tp_price,
    timeInForce='GTC'
)
```

**OCO (spot) - nouveau endpoint :**
```python
# POST /api/v3/orderList/oco
client.create_oco_order(
    symbol='BTCUSDT',
    side='SELL',
    quantity=position.quantity,
    aboveType='TAKE_PROFIT_LIMIT',
    abovePrice=tp_price,
    aboveStopPrice=tp_trigger,
    belowType='STOP_LOSS_LIMIT',
    belowPrice=sl_limit,
    belowStopPrice=sl_price,
)
```

**Close immédiat (margin avec AUTO_REPAY) :**
```python
client.create_margin_order(
    symbol='BTCUSDT',
    side='BUY',  # BUY pour fermer un SHORT
    type='MARKET',
    quantity=position.quantity,
    sideEffectType='AUTO_REPAY',
    isIsolated='TRUE'  # si isolated
)
```

### WebSocket Frontend Protocol

Messages envoyés du backend vers le frontend via WebSocket :

```json
// Mise à jour de position
{
    "type": "position_update",
    "data": {
        "id": 1,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_price": 65000.00,
        "current_price": 67500.00,
        "quantity": 0.01,
        "pnl_usd": 25.00,
        "pnl_pct": 3.85,
        "market_type": "CROSS_MARGIN",
        "sl_price": null,
        "tp_price": null,
        "duration": "2h 15m"
    }
}

// Mise à jour de prix
{
    "type": "price_update",
    "data": {
        "symbol": "BTCUSDT",
        "price": 67500.00,
        "change_24h": 2.5
    }
}

// Mise à jour d'ordre
{
    "type": "order_update",
    "data": {
        "order_id": "123456",
        "symbol": "BTCUSDT",
        "status": "FILLED",
        "type": "STOP_LOSS_LIMIT",
        "price": 60000.00,
        "filled_qty": 0.01
    }
}

// Notification
{
    "type": "notification",
    "data": {
        "level": "success",
        "message": "SL placé sur BTCUSDT @ 60000"
    }
}
```

---

## Dépendances Python

```
# requirements.txt
fastapi==0.115.*
uvicorn[standard]==0.34.*
python-binance==1.0.35
sqlalchemy[asyncio]==2.0.*
aiosqlite==0.20.*
python-dotenv==1.0.*
pydantic==2.*
pydantic-settings==2.*
websockets==13.*
httpx==0.27.*
structlog==24.*
```

---

## Variables d'environnement

```env
# .env.example
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET_KEY=your_api_secret_here

# Stablecoins à ignorer pour la détection de positions (quote currencies, pas des positions)
STABLECOINS=USDT,USDC,BUSD,DAI,TUSD,FDUSD

# Symboles toujours trackés (majors). Les positions ouvertes sont ajoutées automatiquement en plus.
DEFAULT_WATCHLIST=BTCUSDC,ETHUSDC,BNBUSDC

# Intervalle de snapshot des balances (secondes)
BALANCE_SNAPSHOT_INTERVAL=300

# Intervalle d'enregistrement des prix (secondes)
PRICE_RECORD_INTERVAL=60

# Port du serveur
PORT=8001

# Rétention des données prix (jours)
PRICE_RETENTION_DAYS=30

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json

# Notifications Telegram (optionnel)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

---

## Sécurité

- **Pas d'auth sur le dashboard** : le VPS n'est accessible que via Tailscale (réseau privé)
- **Clés API** : stockées dans `.env`, jamais commitées (dans `.gitignore`)
- **Permissions API Binance** : activer uniquement "Enable Spot & Margin Trading", désactiver withdrawal
- **IP Whitelist** : configurer sur Binance l'IP du VPS comme seule IP autorisée
- **HTTPS** : via `tailscale cert` pour obtenir un certificat automatique

---

## Précision décimale

**Règle absolue** : ne jamais utiliser `float` pour des montants financiers.

```python
from decimal import Decimal

# Binance renvoie des strings → convertir directement en Decimal
price = Decimal(trade_data['price'])      # "67523.45" → Decimal('67523.45')
qty = Decimal(trade_data['qty'])          # "0.00150000" → Decimal('0.00150000')
pnl = (current_price - entry_price) * qty  # Calcul exact
```

**En DB** : SQLAlchemy `Numeric` ou stocker en `TEXT` et convertir en Decimal à la lecture.

---

## Filtres Binance (exchangeInfo)

Avant chaque ordre, il faut respecter les filtres de la paire. Sinon → rejet.

| Filtre | Description | Exemple BTCUSDT |
|--------|-------------|-----------------|
| `LOT_SIZE` | Quantité min/max + stepSize | min=0.00001, step=0.00001 |
| `PRICE_FILTER` | Prix min/max + tickSize | tickSize=0.01 |
| `MIN_NOTIONAL` | Valeur minimum de l'ordre | minNotional=5.0 USDT |
| `NOTIONAL` | Valeur min/max | |

**Implémentation** : créer un module `backend/utils/symbol_filters.py` qui :
1. Cache l'`exchangeInfo` au démarrage (et refresh toutes les heures)
2. Expose `round_quantity(symbol, qty)` et `round_price(symbol, price)` qui arrondissent selon les filtres
3. Expose `validate_order(symbol, qty, price)` qui vérifie tous les filtres avant d'envoyer

```python
from decimal import Decimal, ROUND_DOWN

def round_step(value: Decimal, step: Decimal) -> Decimal:
    """Arrondir une valeur au step inférieur le plus proche."""
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step
```

---

## Crash Recovery

Au redémarrage de l'application, la séquence de recovery :

1. **Charger l'état DB** : positions actives, ordres en cours
2. **Synchroniser avec Binance** :
   - Récupérer les balances actuelles → comparer avec les positions DB
   - Récupérer les ordres ouverts → mettre à jour le statut en DB
   - Récupérer les trades récents → détecter les trades manqués pendant le downtime
3. **Upsert** : utiliser les IDs Binance comme clé unique, jamais dupliquer
4. **Résoudre les conflits** :
   - Position DB active mais balance Binance = 0 → marquer comme fermée
   - Balance Binance > 0 mais pas de position DB → créer la position
   - Ordre DB "NEW" mais absent chez Binance → marquer comme "CANCELED" ou "EXPIRED"
5. **Reprendre le temps réel** : relancer les WebSocket streams

---

## Reconnexion WebSocket

```
Stratégie de backoff exponentiel :

Tentative 1 → attendre 1s
Tentative 2 → attendre 2s
Tentative 3 → attendre 4s
Tentative 4 → attendre 8s
...
Max → attendre 60s
Reset du compteur après une connexion stable de 5 minutes
```

Les WebSocket Binance coupent automatiquement après **24 heures**. Le `ws_manager` doit prévoir une reconnexion proactive avant l'expiration.

Si la déconnexion dure plus de **5 minutes** → envoyer une notification (Telegram si configuré) + log ERROR.

---

## Frontend Mobile-First

L'usage principal sera depuis un iPhone via Safari. Design mobile-first :

- **Touch targets** : boutons minimum 44x44px (guideline Apple)
- **Tailwind breakpoints** : concevoir pour `sm` d'abord, puis `md`, puis `lg`
- **Pas de hover-only** : toutes les interactions doivent fonctionner au tap
- **Police lisible** : minimum 14px pour les données de trading
- **Couleurs PnL** : vert (#22c55e) pour positif, rouge (#ef4444) pour négatif
- **Scrollable** : le tableau des positions doit scroller horizontalement sur mobile
- **PWA-ready** : manifest.json + meta viewport pour une expérience app-like

---

## Logging structuré

Tous les logs en format JSON pour faciliter le debug sur VPS :

```python
import structlog

log = structlog.get_logger()

# Exemples
log.info("order_placed", symbol="BTCUSDT", side="SELL", type="STOP_LOSS_LIMIT", price="60000")
log.error("binance_api_error", endpoint="/api/v3/order", status=400, msg="LOT_SIZE filter failure")
log.warning("ws_reconnecting", attempt=3, delay_seconds=4)
```

Niveaux :
- **ERROR** : erreurs API Binance, erreurs DB, ordres rejetés
- **WARNING** : reconnexions WS, rate limit approché, données incohérentes
- **INFO** : trades exécutés, ordres placés/annulés, positions ouvertes/fermées, snapshots balance
- **DEBUG** : prix reçus, messages WS bruts, requêtes API détaillées

---

## Notifications Telegram (optionnel, Phase 5+)

Si `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` sont configurés dans `.env` :

| Événement | Notification |
|-----------|-------------|
| SL touché | "SL hit on BTCUSDT @ 60000 - Position closed, PnL: -$25.00 (-3.8%)" |
| TP touché | "TP hit on BTCUSDT @ 75000 - Position closed, PnL: +$100.00 (+15.4%)" |
| Position fermée manuellement | "Position closed: BTCUSDT LONG, PnL: +$50.00" |
| Erreur critique | "CRITICAL: Binance API unreachable for 5+ minutes" |
| Déconnexion WS prolongée | "WARNING: WebSocket disconnected for 5+ minutes, reconnecting..." |
| App redémarrée | "RootCoin restarted, X positions recovered" |

---

## Notes techniques

### python-binance v1.0.35 - Changements importants
- Le user data stream utilise désormais le nouveau `listenToken` (via `POST /sapi/v1/userListenToken`)
- Les anciens endpoints `POST/PUT/DELETE /api/v3/userDataStream` sont retirés depuis le 20/02/2026
- Support natif Ed25519 et RSA pour la signature
- La signature doit être calculée APRÈS percent-encoding des paramètres (changement du 15/01/2026)

### Tailwind CSS (build)
- Tailwind v3 compilé en production (pas de CDN)
- Source : `frontend/css/tailwind.css` → Build : `frontend/css/output.css`
- Recompiler après toute modification de classes Tailwind dans le HTML/JS :
  ```bash
  npx tailwindcss -i frontend/css/tailwind.css -o frontend/css/output.css --minify
  ```
- Config : `tailwind.config.js` (scan `frontend/**/*.{html,js}`)

### Limitations connues
- La notion de "position" en spot/margin est reconstruite côté application, pas native Binance
- Le prix d'entrée moyen peut être imprécis si les trades sont très anciens (l'API ne retourne que les 500 derniers trades par défaut)
- Les intérêts margin ne sont pas inclus dans le calcul du PnL (amélioration future)
- Un seul utilisateur supporté (pas de multi-user, pas d'auth)