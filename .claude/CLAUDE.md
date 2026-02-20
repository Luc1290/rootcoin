# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projet

Application de trading Binance avec dashboard web (spot + margin cross/isolated). Voir `doc/PROJECT.md` pour le plan complet et la reference API Binance.

## Commandes

- **Dev local** : `uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload`
- **Installer les deps Python** : `pip install -r requirements.txt`
- **Rebuild Tailwind** (apres modif de classes dans HTML/JS) : `npx tailwindcss -i frontend/css/tailwind.css -o frontend/css/output.css --minify`
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
5. `position_tracker.start()` — scan Binance pour reconstruire les positions, puis ecoute les events WS
6. `price_recorder.start()` / `balance_tracker.start()` — s'abonnent aux events WS

L'arret se fait en ordre inverse. Chaque module expose `start()`/`stop()`.

### Pattern event-driven

`ws_manager` est un dispatcher central. Les modules s'abonnent aux events via `ws_manager.on(EVENT_TYPE, callback)`. Les types d'events : `execution_report`, `account_update`, `balance_update`, `list_status`, `price_update`.

### Position tracking

Les positions n'existent pas nativement sur Binance spot/margin — elles sont reconstruites :
- Au demarrage : scan des balances spot/cross/isolated, detection des assets non-stablecoin avec solde > 0, calcul du prix d'entree moyen via l'historique des trades
- En continu : `_handle_execution_report` traite chaque fill pour ouvrir/DCA/reduire/fermer les positions
- Les positions actives sont gardees en memoire dans `_positions: dict[int, Position]` et persistees en DB
- Logique d'ambiguite margin : un BUY margin peut etre un close SHORT ou un open LONG (resolu via l'etat courant + dette)

### Frontend → Backend

- REST API : `routes/api_*.py` — CRUD positions, ordres, balances, trades, prix
- WebSocket : `routes/ws_dashboard.py` — broadcast `positions_snapshot` toutes les 2s + events prix/ordres/balances
- Frontend JS : modules IIFE (`WS`, `App`, `Positions`, `Trades`, `Balances`) communiquent via `WS.on(type, callback)`

## Conventions

- Langue du code : anglais. Langue de communication : francais
- Async partout cote backend (FastAPI + python-binance AsyncClient)
- Pas de commentaires superflus, pas de docstrings sauf API publiques
- Gestion d'erreurs uniquement aux frontieres (appels Binance, input utilisateur)
- Un seul utilisateur, pas de systeme d'auth

## Regles strictes

- **1 fichier = 1 responsabilite**, max ~500-1000 lignes. Decouper en sous-modules si ca grossit
- **Decimal partout** : `from decimal import Decimal` pour tous les prix, quantites, PnL. Jamais de `float` pour de l'argent. Strings Binance → Decimal directement
- **Filtres Binance** : avant chaque ordre, valider via `symbol_filters.validate_order()` et arrondir avec `round_quantity()`/`round_price()`
- **UTC partout en DB** : conversion en local uniquement cote frontend JS
- **Crash recovery** : au redemarrage, reconstruire l'etat complet depuis Binance sans dupliquer en DB (upsert sur IDs Binance)

## API Binance — Points critiques

- **NE PAS se fier aux connaissances du modele** pour les endpoints Binance → consulter `doc/PROJECT.md` section "Reference API Binance"
- Signature HMAC-SHA256 : percent-encoder les params AVANT de signer (changement 15/01/2026)
- User data stream : `POST /sapi/v1/userListenToken` (ancien listenKey retire 20/02/2026)
- OCO spot : `POST /api/v3/orderList/oco` (ancien endpoint deprecie)
- Margin : toujours specifier `sideEffectType` (AUTO_REPAY pour fermer, MARGIN_BUY pour ouvrir)
- Symboles WebSocket en minuscules (`btcusdc@ticker`, pas `BTCUSDC@ticker`)
- `executionReport` : utiliser `l` (last fill qty), PAS `z` (cumulative). Traiter PARTIALLY_FILLED comme FILLED

## Frontend

- **Mobile-first** : iPhone = usage principal. Touch-friendly (boutons min 44px), Tailwind breakpoints sm → md → lg
- **Tailwind CSS v3 compile** : source `frontend/css/tailwind.css` → output `frontend/css/output.css`. Recompiler apres toute modif de classes

## Git

- Commits en anglais, conventionnels : `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`
- Ne jamais commit `.env`, `data/*.db`, `__pycache__/`

## Logging

- `structlog` avec JSON renderer. Niveaux : ERROR (erreurs Binance/reseau), WARNING (reconnexions), INFO (trades/ordres), DEBUG (prix)
