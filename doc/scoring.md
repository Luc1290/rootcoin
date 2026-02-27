# Moteur de Scoring Unifie

> Derniere mise a jour : 2026-02-27

## Vue d'ensemble

Le scoring evalue chaque symbole de la watchlist avec un score unique **0-100** affiche partout (cockpit, page analyse, opportunites). Un seul point de calcul dans `backend/scoring/`, consomme par `market_analyzer` qui orchestre le tout.

**Philosophie** : scoring par **confluence a couches**. Chaque timeframe et source de donnees a un budget de points fixe. Plus il y a de confluence entre les couches, plus le score monte. Pas de moyenne diluante.

## Architecture

```
market_analyzer._analyze_symbol(symbol)
    │
    ├── _compute_key_levels(symbol)          → pivots + swings 4h
    │
    ├── signal_engine.extract_signals("15m") → tendance + momentum + structure
    ├── signal_engine.extract_signals("1h")  → tendance + momentum
    ├── signal_engine.extract_signals("4h")  → tendance + momentum
    │
    └── scorer.compute_unified_score(...)    → score 0-100
            ├── Layer 1 : 15m primaire        (0-40 pts)
            ├── Layer 2 : 1h confirmation     (0-25 pts)
            ├── Layer 3 : 4h contexte         (0-20 pts)
            ├── Layer 4 : flux temps reel     (0-15 pts)
            └── Layer 5 : macro contexte      (-10 a +5 pts)
                                              ─────────────
                                    Total max : 105 → normalise 0-100
```

## Les 5 couches

### Layer 1 — 15m Primaire (0-40 pts)

C'est la couche dominante. Le 15m est le timeframe de decision.

| Composant | Budget | Indicateurs | Logique |
|-----------|--------|-------------|---------|
| **Tendance** | 0-15 | MACD histogram + MA7/MA25 | MACD > 0 et croissant = 8 pts, MA7 > MA25 et prix > MA25 = 7 pts |
| **Momentum** | 0-15 | RSI + StochRSI + MFI | RSI < 30 = 6 pts, StochRSI cross haussier en survente = 5 pts, MFI < 20 = 4 pts |
| **Structure** | 0-10 | Detection sur les 16 dernieres bougies 15m | Voir section dediee ci-dessous |

La direction (LONG/SHORT) est determinee par cette couche : si la tendance 15m est haussiere, le biais est LONG.

### Layer 2 — 1h Confirmation (0-25 pts)

Le 1h ne decide pas de la direction, il confirme ou infirme le 15m.

| Situation | Tendance (0-15) | Momentum (0-10) |
|-----------|----------------|-----------------|
| 1h aligne avec 15m | Score tendance 1h tel quel | Score momentum × 10/15 |
| 1h neutre | 5 pts forfaitaires | 3 pts forfaitaires |
| 1h oppose au 15m | 0 pts | 0 pts |

### Layer 3 — 4h Contexte (0-20 pts)

Le 4h donne le contexte de tendance large. Un setup 15m valide par la tendance 4h est nettement plus fort.

| Situation | Tendance (0-15) | Bonus structure (0-5) |
|-----------|----------------|----------------------|
| 4h aligne | Score tendance 4h | +5 (solidite structurelle) |
| 4h neutre | 5 pts | +2 |
| 4h oppose | 0 pts | 0 |

### Layer 4 — Flux temps reel (0-15 pts)

Microstructure de marche en direct.

| Source | Budget | Seuil | Logique |
|--------|--------|-------|---------|
| **Buy/Sell pressure** | 0-5 | B/S > 5% | Pression taker dans la direction du biais |
| **Orderbook imbalance** | 0-5 | Imbalance > 0.05 | Desequilibre bid/ask dans la direction |
| **Whale activity** | 0-5 | Trade > seuil dans les 10 dernieres min | Gros trade dans la direction du biais |

### Layer 5 — Macro (-10 a +5 pts)

Le macro est un **contexte seulement**, pas un driver. Il ne peut pas pousser le score vers le haut significativement, mais peut penaliser si l'environnement est fortement oppose.

| Condition | Points |
|-----------|--------|
| Macro fortement oppose (avg < -0.4) | -10 |
| Macro legerement oppose (-0.4 a -0.2) | -5 |
| Macro neutre (-0.2 a +0.2) | 0 |
| Macro legerement aligne (+0.2 a +0.4) | +3 |
| Macro fortement aligne (> +0.4) | +5 |

Indicateurs macro pris en compte : DXY, VIX, Nasdaq, Gold, US10Y, USD/JPY (chacun avec son poids).

## Detection de structure (Layer 1)

C'est l'innovation principale. Au lieu de ne regarder que la derniere bougie, le systeme analyse les **16 dernieres bougies 15m** (~4h de lookback, correspondant a la duree max typique d'un trade).

### Rejet par meche (0-4 pts)

Detecte les bougies avec une meche significative pres d'un niveau cle.

- **Condition LONG** : meche basse >= 2× corps ET le bas de la bougie est a 0.3% ou moins d'un support
- **Condition SHORT** : meche haute >= 2× corps ET le haut est a 0.3% ou moins d'une resistance
- Meche >= 3× corps : 4 pts, meche >= 2× corps : 3 pts

### Tests de niveau (0-3 pts)

Compte combien de bougies dans la fenetre touchent le meme niveau puis repartent.

- **Condition LONG** : le bas touche un support (0.3% de tolerance) et la cloture est au-dessus
- 1 test = 1 pt, 2 tests = 2 pts, 3+ tests = 3 pts
- Plusieurs tests du meme niveau = signal fort (double/triple bottom)

### Break-and-retest (0-3 pts)

Detecte une cassure de niveau suivie d'un retour pour le tester depuis l'autre cote.

- **Condition LONG** : le prix casse sous un support (cloture sous le niveau) puis revient cloturer au-dessus
- Si detecte : 3 pts

## Niveaux cles utilises

Les niveaux viennent de deux sources :

1. **Pivots classiques** du jour (daily) : R2, R1, Pivot, S1, S2
2. **Swing highs/lows** des 30 dernieres bougies 4h (~5 jours)

Les niveaux proches (< 0.3%) sont dedupliques, les pivots ayant priorite sur les swings.

## Echelle de scores

| Score | Interpretation | Scenario typique |
|-------|----------------|------------------|
| 0-20 | Signaux faibles / contradictoires | Marche indecis, pas de setup clair |
| 20-40 | Setup 15m seul, pas de confirmation | Signal 15m present mais 1h/4h neutres ou opposes |
| 40-55 | Setup confirme en 1h | 15m + 1h alignes, structure ou flow partiels |
| 55-70 | Bonne confluence | 15m + 1h + structure, flux favorable |
| 70-85 | Forte confluence | Tout aligne (15m + 1h + 4h + flow), macro neutre ou positif |
| 85-100 | Confluence maximale | Tous les signaux alignes y compris macro favorable |

## Direction et hysterese

La direction est determinee par la couche 15m. Si le 15m est neutre, le 1h prend le relais. Si tout est neutre, LONG par defaut.

**Hysterese** : la direction precedente est gardee sauf si le nouveau score dans le sens oppose depasse 30. Cela evite les changements de biais sur du bruit.

## Opportunites

Les opportunites (popups cockpit) ne sont plus un calcul separe. C'est un **filtre** sur le score unifie :

- Symboles sans position ouverte
- Score >= `opportunity_min_score` (defaut 40)
- Cooldown de 30 min par symbole apres detection
- Le message et les signaux cles sont extraits de l'analyse existante

## Logs

Chaque cycle de scoring emet un log visible dans la page Health :

```
scoring_result symbol=BTCUSDC direction=LONG score=62 raw=65.1
  L1_15m=28.0/40 (T15.0 M8.0 S5.0) L2_1h=18.0/25 L3_4h=12.0/20
  L4_flow=7.1/15 L5_macro=0.0
```

- **T** = tendance, **M** = momentum, **S** = structure
- **L1-L5** = score de chaque couche / max possible

## Fichiers

| Fichier | Role |
|---------|------|
| `backend/scoring/signal_engine.py` | Extraction signaux + detection structure par timeframe |
| `backend/scoring/scorer.py` | Combinaison des couches en score 0-100 |
| `backend/market/market_analyzer.py` | Orchestration : key levels, appels scoring, alerts, justification |
| `backend/market/opportunity_detector.py` | Filtre sur score unifie, messages FR, cooldown |
| `backend/market/analysis_formatter.py` | Descriptions FR des signaux (y compris structure) |
