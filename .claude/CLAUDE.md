# RootCoin - Instructions pour Claude Code

## Projet
Application de trading Binance avec dashboard web. Voir `doc/PROJECT.md` pour le plan complet.

## Stack
- **Backend** : Python 3.11+ / FastAPI / python-binance 1.0.35+
- **DB** : SQLite via SQLAlchemy async + aiosqlite
- **Frontend** : HTML/CSS/JS vanilla + Tailwind CSS CDN + TradingView Lightweight Charts
- **Temps réel** : WebSocket (Binance → Backend → Frontend)
- **Déploiement** : systemd + Tailscale sur VPS, code via GitHub

## Structure
```
rootcoin/
├── backend/
│   ├── main.py              # Point d'entrée FastAPI
│   ├── config.py            # Configuration .env
│   ├── database.py          # SQLAlchemy engine + session
│   ├── models.py            # Modèles DB (positions, trades, orders, balances, prices, settings)
│   ├── binance_client.py    # Wrapper python-binance (singleton async)
│   ├── position_tracker.py  # Détection et suivi des positions
│   ├── order_manager.py     # SL/TP/OCO/Close
│   ├── price_recorder.py    # Enregistrement prix périodique
│   ├── balance_tracker.py   # Snapshots balances
│   ├── ws_manager.py        # WebSocket Binance (user data + prix)
│   ├── routes/              # Routes FastAPI (REST + WS)
│   └── utils/
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/                  # app.js, websocket.js, positions.js, orders.js, balances.js, charts.js
├── data/                    # rootcoin.db (auto-créé)
└── scripts/                 # setup_vps.sh, backup_db.sh
```

## Règles strictes
- **1 fichier = 1 responsabilité** : ne jamais entasser plusieurs fonctionnalités dans un même fichier
- **Max ~500-1000 lignes par fichier** : si un fichier dépasse, le découper en sous-modules
- **Nouveau fichier pour chaque nouvelle fonction/feature** : pas de fichiers fourre-tout
- Exemple : `order_manager/` devient un dossier avec `stop_loss.py`, `take_profit.py`, `oco.py`, `close.py` si ça grossit

## Conventions
- Langue du code : anglais (noms de variables, fonctions, commentaires techniques)
- Langue de communication : français
- Async partout côté backend (FastAPI + python-binance AsyncClient)
- Pas de commentaires superflus dans le code, seulement quand la logique n'est pas évidente
- Pas de docstrings sauf pour les fonctions d'API publiques
- Gestion d'erreurs uniquement aux frontières (appels Binance, input utilisateur)
- Un seul utilisateur, pas de système d'auth (accès via Tailscale uniquement)

## Règles financières / trading
- **Decimal partout** : utiliser `from decimal import Decimal` pour tous les prix, quantités, PnL. Jamais de `float` pour de l'argent
- **Strings Binance → Decimal** : l'API Binance renvoie des strings, les convertir directement en `Decimal`
- **Filtres Binance** : avant chaque ordre, valider via `exchangeInfo` (LOT_SIZE, MIN_NOTIONAL, PRICE_FILTER, STEP_SIZE). Arrondir les quantités/prix selon les règles de la paire
- **UTC partout en DB** : tous les timestamps en UTC. Conversion en local uniquement côté frontend JS
- **Paires USDC** : l'utilisateur trade principalement des paires en USDC. USDC est la quote currency, pas un token à tracker
- **Watchlist dynamique** : BTCUSDC/ETHUSDC/BNBUSDC toujours trackés (majors) + automatiquement tout token avec position ouverte + ajout manuel depuis le dashboard. Peut être n'importe quel token, même un nouveau
- **Crash recovery** : au redémarrage, reconstruire l'état complet depuis Binance (positions, ordres ouverts) sans dupliquer les données existantes en DB (upsert sur les IDs Binance)

## WebSocket / Réseau
- **Reconnexion auto** : backoff exponentiel (1s, 2s, 4s, 8s... max 60s) pour les WS Binance et frontend
- **Heartbeat** : répondre aux pings Binance sous 60s, les WS coupent après 24h max → reconnecter
- **Graceful shutdown** : à l'arrêt, fermer proprement les connexions WS, sauvegarder l'état en DB

## Frontend
- **Mobile-first** : l'iPhone sera l'usage principal. Concevoir d'abord pour petit écran, puis adapter desktop
- **Responsive** : Tailwind breakpoints (sm → md → lg), touch-friendly (boutons min 44px)
- **Tailwind CSS v3 compilé** : après toute modif de classes Tailwind → `npx tailwindcss -i frontend/css/tailwind.css -o frontend/css/output.css --minify`

## Logging / Monitoring
- **Logging structuré JSON** : utiliser `structlog` ou le module `logging` avec un formatter JSON
- **Niveaux** : ERROR pour les erreurs Binance/réseau, WARNING pour les reconnexions, INFO pour les trades/ordres, DEBUG pour le reste
- **Notifications Telegram** (future) : alerter sur SL/TP touché, erreur critique, déconnexion prolongée

## Git
- Commits en anglais, conventionnels : `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`
- Ne jamais commit `.env`, `data/*.db`, `__pycache__/`

## API Binance - Points critiques
- **NE PAS se fier aux connaissances du modèle** pour les endpoints Binance → consulter `PROJECT.md` section "Référence API Binance"
- Signature HMAC-SHA256 : percent-encoder les params AVANT de signer (changement 15/01/2026)
- User data stream : utiliser `POST /sapi/v1/userListenToken` (ancien listenKey retiré 20/02/2026)
- OCO spot : `POST /api/v3/orderList/oco` (ancien endpoint `/api/v3/order/oco` déprécié)
- Margin : toujours spécifier `sideEffectType` (AUTO_REPAY pour fermer, MARGIN_BUY pour ouvrir)
- Symboles WebSocket en minuscules (`btcusdt@ticker`, pas `BTCUSDT@ticker`)

## Commandes
- **Dev local** : `uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload`
- **Installer les deps** : `pip install -r requirements.txt`
- **VPS deploy** : `git pull && sudo systemctl restart rootcoin`
- **Logs VPS** : `journalctl -u rootcoin -f`
- **Status VPS** : `sudo systemctl status rootcoin`
