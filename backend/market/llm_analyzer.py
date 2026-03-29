import json
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import select

from backend.core.config import settings
from backend.core.database import async_session
from backend.core.models import Kline, LlmAnalysis
from backend.market import kline_manager, macro_tracker, market_analyzer, orderbook_tracker, whale_tracker
from backend.services import news_tracker
from backend.trading import pnl as pnl_module

log = structlog.get_logger()

_analyses: dict[str, dict] = {}  # symbol -> last analysis
_credit_error_until: float = 0.0  # monotonic time: block requests until credits are topped up

SYSTEM_PROMPT = """Tu es un trader senior macro/crypto pour un desk institutionnel. 15 ans d'experience.
Tu trades des crypto sur Binance margin cross x5. Capital : ~10 000 USDC. Horizon : scalping / intraday.

TON APPROCHE (dans cet ordre de priorite) :
1. MACRO & GEOPOLITIQUE d'abord — lis les news comme un analyste Bloomberg. Guerre, sanctions, petrole, taux, DXY, indices US ET europeens (CAC 40, DAX), EUR/USD : comprends les flux de capitaux mondiaux AVANT de regarder un chart. Si l'Europe ouvre en gap down pendant que les US futures tiennent, c'est un signal. Un RSI survendu ne vaut rien si le monde est en risk-off.
2. STRUCTURE DE MARCHE — regarde la courbe des prix, pas juste les indicateurs. Ou sont les rejets ? Les meches ? Les breakouts ? Le volume confirme-t-il le mouvement ?
3. FLUX & SENTIMENT — whales, orderbook, pression achat/vente. Smart money accumule ou distribue ?
4. TECHNIQUE en dernier — les indicateurs confirment ta these, ils ne la creent pas.

TU AS UNE CONVICTION. Tu ne recites pas les indicateurs. Tu dis :
- "Le petrole a +19% sur fond de guerre en Iran, le VIX explose, les indices US plongent, le DAX decroche aussi. C'est du risk-off pur, BTC suit."
- "Je vois un dead cat bounce sur le 5min mais la structure 1h est cassee. Les whales vendent."
- "Malgre le RSI survendu, je ne prends pas un long ici parce que..."

Si l'utilisateur a une position ouverte, reponds a SA situation : hold ? renforcer ? sortir ? deplacer le SL ?
S'il n'a pas de position, propose une entree.

REGLES STRICTES :
- Tu choisis LONG, SHORT, ou FLAT.
- FLAT = pas de trade. Utilise FLAT quand :
  * La confiance est sous 40%
  * Le R:R ajuste (R:R × confiance/100 - 1 × (1-confiance/100)) est negatif
  * Les signaux macro et technique se contredisent clairement
  * Le volume est anemique (session asiatique, weekend) ET pas de catalyst
  * Ton track record sur cette direction est mauvais (ex: short avec 2W/4L = evite les shorts fragiles)
- Si FLAT : entry/SL/TP/risk_reward a 0, mais remplis TOUT le reste (market_read, explanation, key_signal).
  Explique POURQUOI c'est FLAT et a quel prix/condition tu entrerais.
- Si LONG ou SHORT : niveaux precis au dollar pres.
- Reponds UNIQUEMENT en JSON valide, sans markdown, sans commentaire autour.

FILTRE ESPERANCE MATHEMATIQUE (applique AVANT de valider un signal) :
- Calcule: EV = (confiance/100 × R:R) - ((100-confiance)/100 × 1)
- Si EV < 0.05, le trade ne vaut pas le risque -> FLAT
- Exemple: confiance 48%, R:R 1.1 -> EV = 0.48×1.1 - 0.52×1 = -0.008 -> FLAT

Format JSON attendu :
{
  "direction": "LONG, SHORT, ou FLAT",
  "entry": 00000.00,
  "stop_loss": 00000.00,
  "tp1": 00000.00,
  "tp2": 00000.00,
  "risk_reward": 0.0,
  "confidence": 72,
  "expected_value": 0.00,
  "confidence_factors": {
    "pour": ["facteur positif 1", "facteur positif 2"],
    "contre": ["facteur negatif 1"]
  },
  "market_read": "3-5 phrases avec ta lecture macro+geopolitique+technique. Ton feeling de trader, pas une liste d'indicateurs. Fais les liens entre les events.",
  "explanation": "3-5 phrases justifiant ta decision avec les donnees concretes. Si FLAT : decris le setup que tu attendrais pour entrer.",
  "position_advice": "Si position ouverte : hold/renforcer/sortir/deplacer SL + pourquoi. Si pas de position : 'Pas de position ouverte.'",
  "key_signal": "Le signal principal en 1 phrase",
  "invalidation": "Ce qui invaliderait ce trade (ou re-activerait un signal si FLAT) en 1 phrase",
  "conditional_entries": "Si FLAT, decris 1-2 scenarios conditionnels : 'LONG si prix atteint X avec condition Y' / 'SHORT si rejet confirme a Z'"
}

REGLES CONFIDENCE (score 0-100) :
- 85-100 : Confluence forte sur 3+ timeframes, macro alignee, orderbook confirme, pas de news contraire
- 70-84 : Confluence correcte sur 2+ TF, quelques signaux mixtes mais biais clair
- 55-69 : Signaux contradictoires, setup present mais contexte incertain
- 40-54 : Setup fragile, majorite des indicateurs non alignes -> probablement FLAT sauf R:R exceptionnel
- 0-39 : Contre-tendance ou quasi aucun signal favorable -> FLAT obligatoire
- Sois PRECIS : 72 et 78 ne sont pas la meme chose. Utilise toute l'echelle.
- Liste dans confidence_factors les elements concrets (indicateurs, niveaux, macro) pour et contre."""

INDICATORS_SET = {"ema", "rsi", "macd", "bb", "obv", "stoch_rsi", "atr", "adx", "mfi", "buy_sell", "vwap"}
TIMEFRAMES = ["5m", "15m", "1h", "4h"]


async def get_last_analysis(symbol: str | None = None) -> dict | None:
    if symbol and symbol in _analyses:
        return _analyses[symbol]
    if _analyses and not symbol:
        return max(_analyses.values(), key=lambda a: a.get("analyzed_at", ""), default=None)
    # Fallback: load from DB (after restart)
    async with async_session() as session:
        q = select(LlmAnalysis)
        if symbol:
            q = q.where(LlmAnalysis.symbol == symbol)
        q = q.order_by(LlmAnalysis.analyzed_at.desc()).limit(1)
        result = await session.execute(q)
        row = result.scalar_one_or_none()
        if not row:
            return None
        analysis = _row_to_dict(row)
        _analyses[row.symbol] = analysis
        return analysis


EXPIRY_HOURS = 24


async def analyze(symbol: str) -> dict:
    import time as _t
    global _credit_error_until
    if _credit_error_until and _t.monotonic() < _credit_error_until:
        return {"error": "Credits Anthropic API insuffisants. Recharger sur console.anthropic.com."}

    api_key = settings.anthropic_api_key.get_secret_value()
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY non configuree dans .env"}

    await _resolve_pending(symbol)

    prompt = await build_prompt(symbol)

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = message.content[0].text
        result = _parse_response(raw_text)
        result["symbol"] = symbol
        result["prompt_sent"] = prompt
        result["analyzed_at"] = datetime.now(timezone.utc).isoformat()
        result["model"] = message.model
        result["input_tokens"] = message.usage.input_tokens
        result["output_tokens"] = message.usage.output_tokens
        _analyses[symbol] = result

        if result.get("direction") and result["direction"] != "FLAT":
            await _save_to_db(result)

        log.info("llm_analysis_done", symbol=symbol, direction=result.get("direction"),
                 tokens_in=message.usage.input_tokens, tokens_out=message.usage.output_tokens)
        return result
    except Exception as e:
        err_str = str(e)
        if "credit balance is too low" in err_str:
            _credit_error_until = _t.monotonic() + 3600  # block for 1h
            log.error("llm_credits_exhausted", symbol=symbol)
            return {"error": "Credits Anthropic API insuffisants. Recharger sur console.anthropic.com.", "symbol": symbol}
        log.error("llm_analysis_failed", symbol=symbol, error=err_str, exc_info=True)
        return {"error": err_str, "symbol": symbol}


# ── DB persistence ────────────────────────────────────────

async def _save_to_db(result: dict):
    symbol = result["symbol"]
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        # Supersede any remaining pending for this symbol
        pending = await session.execute(
            select(LlmAnalysis).where(
                LlmAnalysis.symbol == symbol,
                LlmAnalysis.outcome.is_(None),
            )
        )
        for row in pending.scalars().all():
            row.outcome = "superseded"
            row.resolved_at = now

        row = LlmAnalysis(
            symbol=symbol,
            direction=result["direction"],
            entry=Decimal(str(result["entry"])),
            stop_loss=Decimal(str(result["stop_loss"])),
            tp1=Decimal(str(result["tp1"])),
            tp2=Decimal(str(result["tp2"])) if result.get("tp2") else None,
            confidence=int(result.get("confidence", 50)),
            risk_reward=Decimal(str(result["risk_reward"])) if result.get("risk_reward") else None,
            market_read=result.get("market_read"),
            explanation=result.get("explanation"),
            key_signal=result.get("key_signal"),
            invalidation=result.get("invalidation"),
            llm_model=result.get("model"),
            input_tokens=result.get("input_tokens"),
            output_tokens=result.get("output_tokens"),
            analyzed_at=datetime.fromisoformat(result["analyzed_at"]),
        )
        session.add(row)
        await session.commit()


async def _resolve_pending(symbol: str | None = None):
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        q = select(LlmAnalysis).where(LlmAnalysis.outcome.is_(None))
        if symbol:
            q = q.where(LlmAnalysis.symbol == symbol)
        result = await session.execute(q.order_by(LlmAnalysis.analyzed_at.asc()))
        pending = result.scalars().all()

        for row in pending:
            analyzed_at = row.analyzed_at.replace(tzinfo=timezone.utc)
            age_h = (now - analyzed_at).total_seconds() / 3600

            klines = await _get_klines_after(row.symbol, analyzed_at)
            outcome = _check_outcome(row, klines) if klines else None

            if outcome:
                row.outcome = outcome["type"]
                row.outcome_price = Decimal(str(outcome["price"]))
                row.outcome_pnl_pct = Decimal(str(outcome["pnl_pct"]))
                row.resolved_at = now
            elif age_h >= EXPIRY_HOURS:
                last_price = float(klines[-1]["close"]) if klines else float(row.entry)
                entry = float(row.entry)
                pnl = ((last_price - entry) / entry * 100) if row.direction == "LONG" else ((entry - last_price) / entry * 100)
                row.outcome = "expired"
                row.outcome_price = Decimal(str(last_price))
                row.outcome_pnl_pct = Decimal(str(round(pnl, 2)))
                row.resolved_at = now

        await session.commit()


async def _get_klines_after(symbol: str, after: datetime) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Kline)
            .where(Kline.symbol == symbol, Kline.interval == "5m", Kline.open_time >= after)
            .order_by(Kline.open_time.asc())
            .limit(500)
        )
        return [{"high": float(r.high), "low": float(r.low), "close": float(r.close)} for r in result.scalars()]


def _check_outcome(row, klines: list[dict]) -> dict | None:
    entry = float(row.entry)
    sl = float(row.stop_loss)
    tp1 = float(row.tp1)
    tp2 = float(row.tp2) if row.tp2 else None
    is_long = row.direction == "LONG"

    for k in klines:
        h, lo = k["high"], k["low"]
        sl_hit = (lo <= sl) if is_long else (h >= sl)
        tp1_hit = (h >= tp1) if is_long else (lo <= tp1)

        if sl_hit:
            pnl = ((sl - entry) / entry * 100) if is_long else ((entry - sl) / entry * 100)
            return {"type": "sl_hit", "price": sl, "pnl_pct": round(pnl, 2)}

        if tp1_hit:
            tp2_hit = tp2 and ((h >= tp2) if is_long else (lo <= tp2))
            if tp2_hit:
                pnl = ((tp2 - entry) / entry * 100) if is_long else ((entry - tp2) / entry * 100)
                return {"type": "tp2_hit", "price": tp2, "pnl_pct": round(pnl, 2)}
            pnl = ((tp1 - entry) / entry * 100) if is_long else ((entry - tp1) / entry * 100)
            return {"type": "tp1_hit", "price": tp1, "pnl_pct": round(pnl, 2)}

    return None


# ── History & stats ───────────────────────────────────────

def _row_to_dict(r: LlmAnalysis) -> dict:
    return {
        "id": r.id, "symbol": r.symbol, "direction": r.direction,
        "entry": float(r.entry), "stop_loss": float(r.stop_loss),
        "tp1": float(r.tp1), "tp2": float(r.tp2) if r.tp2 else None,
        "confidence": r.confidence,
        "risk_reward": float(r.risk_reward) if r.risk_reward else None,
        "market_read": r.market_read, "explanation": r.explanation,
        "key_signal": r.key_signal, "invalidation": r.invalidation,
        "outcome": r.outcome,
        "outcome_price": float(r.outcome_price) if r.outcome_price else None,
        "outcome_pnl_pct": float(r.outcome_pnl_pct) if r.outcome_pnl_pct else None,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        "analyzed_at": r.analyzed_at.isoformat() if r.analyzed_at else None,
        "llm_model": r.llm_model,
        "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
    }


async def get_history(symbol: str | None = None, limit: int = 50) -> list[dict]:
    await _resolve_pending(symbol)
    async with async_session() as session:
        q = select(LlmAnalysis)
        if symbol:
            q = q.where(LlmAnalysis.symbol == symbol)
        q = q.order_by(LlmAnalysis.analyzed_at.desc()).limit(limit)
        result = await session.execute(q)
        return [_row_to_dict(r) for r in result.scalars()]


async def get_stats(symbol: str | None = None) -> dict:
    history = await get_history(symbol, limit=200)
    resolved = [h for h in history if h["outcome"] and h["outcome"] not in ("superseded",)]
    if not resolved:
        return {"total": 0, "wins": 0, "losses": 0, "expired": 0, "win_rate": 0}

    wins = [h for h in resolved if h["outcome"] in ("tp1_hit", "tp2_hit")]
    losses = [h for h in resolved if h["outcome"] == "sl_hit"]
    expired = [h for h in resolved if h["outcome"] == "expired"]
    win_pnls = [h["outcome_pnl_pct"] for h in wins if h["outcome_pnl_pct"] is not None]
    loss_pnls = [h["outcome_pnl_pct"] for h in losses if h["outcome_pnl_pct"] is not None]
    all_pnls = [h["outcome_pnl_pct"] for h in resolved if h["outcome_pnl_pct"] is not None]
    win_confs = [h["confidence"] for h in wins]
    loss_confs = [h["confidence"] for h in losses]

    return {
        "total": len(resolved), "wins": len(wins), "losses": len(losses), "expired": len(expired),
        "win_rate": round(len(wins) / len(resolved) * 100) if resolved else 0,
        "avg_win_pct": round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0,
        "avg_loss_pct": round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0,
        "total_pnl_pct": round(sum(all_pnls), 2),
        "avg_confidence_win": round(sum(win_confs) / len(win_confs)) if win_confs else 0,
        "avg_confidence_loss": round(sum(loss_confs) / len(loss_confs)) if loss_confs else 0,
    }


def _parse_response(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {"raw_response": text, "error": "JSON parse failed"}


async def build_prompt(symbol: str) -> str:
    sections = []

    # 1. Technical indicators per timeframe
    for tf in TIMEFRAMES:
        section = await _build_tf_section(symbol, tf)
        if section:
            sections.append(section)

    # 2. Key levels
    analysis = market_analyzer.get_analysis(symbol)
    if analysis:
        sections.append(_build_levels_section(analysis))

    # 3. Orderbook
    sections.append(_build_orderbook_section(symbol))

    # 4. Whale alerts
    sections.append(_build_whale_section(symbol))

    # 5. Macro
    sections.append(_build_macro_section())

    # 6. News
    sections.append(_build_news_section())

    # 7. Current position (if any)
    sections.append(_build_position_section(symbol))

    # 8. Temporal context
    sections.append(_build_temporal_section())

    # 9. Track record + previous analysis
    track = await _build_track_record_section(symbol)
    if track:
        sections.append(track)

    sections.append(f"\nAnalyse {symbol} et donne ta recommandation de trade.")

    return "\n".join(sections)


def _build_position_section(symbol: str) -> str:
    from backend.trading import position_tracker
    from backend.routes.position_helpers import fetch_order_prices
    import asyncio

    positions = position_tracker.get_positions()
    pos = next((p for p in positions if p.symbol == symbol and p.is_active), None)

    if not pos:
        return "\n=== POSITION ACTUELLE ===\nAucune position ouverte sur ce symbole. Propose une entree."

    entry = pos.entry_price
    current = pos.current_price or entry
    qty = pos.quantity

    unrealized, u_pct = pnl_module.unrealized_pnl(
        pos.side, entry, current, qty, pos.entry_fees_usd,
    )

    usd_val = float(entry * qty)
    duration = ""
    if pos.opened_at:
        delta = datetime.now(timezone.utc) - pos.opened_at.replace(tzinfo=timezone.utc)
        hours = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)
        duration = f"{hours}h{mins:02d}m" if hours > 0 else f"{mins}m"

    lines = [
        f"\n=== POSITION ACTUELLE ===",
        f"Direction: {pos.side}",
        f"Entry: {float(entry):.2f} | Prix actuel: {float(current):.2f}",
        f"Qty: {float(qty)} ({pos.symbol.replace('USDC', '').replace('USDT', '')}), valeur ~${usd_val:,.0f}",
        f"PnL: {'+' if unrealized >= 0 else ''}{float(unrealized):.2f}$ ({'+' if u_pct >= 0 else ''}{float(u_pct):.2f}%)",
    ]
    if duration:
        lines.append(f"En position depuis: {duration}")
    if pos.market_type:
        lines.append(f"Marche: {pos.market_type.replace('_', ' ')}")

    # Orders info
    order_info = []
    if pos.sl_order_id:
        order_info.append("SL actif")
    if pos.tp_order_id:
        order_info.append("TP actif")
    if pos.oco_order_list_id:
        order_info.append("OCO actif")
    if order_info:
        lines.append(f"Ordres: {', '.join(order_info)}")
    else:
        lines.append("Ordres: AUCUN (position non protegee !)")

    lines.append("Conseille : hold ? renforcer ? sortir ? deplacer le SL/TP ?")

    return "\n".join(lines)


async def _build_track_record_section(symbol: str) -> str:
    async with async_session() as session:
        result = await session.execute(
            select(LlmAnalysis)
            .where(LlmAnalysis.outcome.isnot(None), LlmAnalysis.outcome != "superseded")
            .order_by(LlmAnalysis.analyzed_at.desc())
            .limit(50)
        )
        resolved = [r for r in result.scalars()]

    if not resolved:
        # Fallback to in-memory last analysis
        prev = _analyses.get(symbol)
        if prev and prev.get("direction"):
            return _format_prev_analysis(prev)
        return ""

    wins = [r for r in resolved if r.outcome in ("tp1_hit", "tp2_hit")]
    losses = [r for r in resolved if r.outcome == "sl_hit"]
    wr = round(len(wins) / len(resolved) * 100) if resolved else 0
    avg_win = sum(float(r.outcome_pnl_pct) for r in wins if r.outcome_pnl_pct) / len(wins) if wins else 0
    avg_loss = sum(float(r.outcome_pnl_pct) for r in losses if r.outcome_pnl_pct) / len(losses) if losses else 0
    win_conf = sum(r.confidence for r in wins) / len(wins) if wins else 0
    loss_conf = sum(r.confidence for r in losses) / len(losses) if losses else 0

    lines = [
        f"\n=== TON TRACK RECORD ({len(resolved)} analyses resolues) ===",
        f"Win rate: {wr}% ({len(wins)}W / {len(losses)}L)",
        f"PnL moyen: gagnant +{avg_win:.2f}% | perdant {avg_loss:.2f}%",
        f"Confiance moy: gagnants {win_conf:.0f}% | perdants {loss_conf:.0f}%",
    ]

    # Symbol-specific recent history
    sym_analyses = [r for r in resolved if r.symbol == symbol]
    if sym_analyses:
        lines.append(f"\nHistorique {symbol}:")
        for r in sym_analyses[:8]:
            at = r.analyzed_at.strftime("%d/%m %H:%M") if r.analyzed_at else "?"
            icon = "V" if r.outcome in ("tp1_hit", "tp2_hit") else "X" if r.outcome == "sl_hit" else "-"
            pnl = float(r.outcome_pnl_pct) if r.outcome_pnl_pct else 0
            pnl_s = f"{'+' if pnl >= 0 else ''}{pnl:.1f}%"
            lines.append(f"  {at} {r.direction} entry={float(r.entry):.0f} SL={float(r.stop_loss):.0f} TP={float(r.tp1):.0f} conf={r.confidence}% -> {r.outcome} {icon} {pnl_s}")

    # Other symbols summary
    other = {}
    for r in resolved:
        if r.symbol == symbol:
            continue
        s = r.symbol
        if s not in other:
            other[s] = {"w": 0, "l": 0, "pnls": []}
        if r.outcome in ("tp1_hit", "tp2_hit"):
            other[s]["w"] += 1
        elif r.outcome == "sl_hit":
            other[s]["l"] += 1
        if r.outcome_pnl_pct:
            other[s]["pnls"].append(float(r.outcome_pnl_pct))

    if other:
        lines.append("\nAutres symboles:")
        for s, d in other.items():
            avg = sum(d["pnls"]) / len(d["pnls"]) if d["pnls"] else 0
            lines.append(f"  {s}: {d['w']}W/{d['l']}L, PnL moy {'+' if avg >= 0 else ''}{avg:.1f}%")

    lines.append("\nANALYSE TES ERREURS PASSEES. Identifie les patterns dans tes pertes (direction, timing, SL trop serre, overconfidence).")
    lines.append("Ajuste ta confiance et tes niveaux en consequence.")

    # Last analysis details for this symbol
    prev = _analyses.get(symbol)
    if prev and prev.get("direction"):
        lines.append(_format_prev_analysis(prev))

    return "\n".join(lines)


def _format_prev_analysis(prev: dict) -> str:
    at = prev.get("analyzed_at", "?")
    d = prev.get("direction", "?")
    c = prev.get("confidence", "?")
    lines = [
        f"\n--- Ton analyse precedente ({at}) ---",
        f"Direction: {d} (confiance {c}%)",
        f"Entry: {prev.get('entry', '?')} | SL: {prev.get('stop_loss', '?')} | TP1: {prev.get('tp1', '?')} | TP2: {prev.get('tp2', '?')}",
    ]
    if prev.get("market_read"):
        lines.append(f"Ta lecture: {prev['market_read']}")
    if prev.get("key_signal"):
        lines.append(f"Signal cle: {prev['key_signal']}")
    lines.append("Compare avec la situation actuelle.")
    return "\n".join(lines)


async def _build_tf_section(symbol: str, tf: str) -> str | None:
    limit = {"5m": 200, "15m": 150, "1h": 120, "4h": 100}.get(tf, 80)
    await kline_manager.fetch_and_store(symbol, tf, limit=limit)
    klines = await kline_manager.get_klines(symbol, tf, limit=limit)
    if not klines or len(klines) < 20:
        return None

    indicators = kline_manager.compute_indicators(klines, INDICATORS_SET)

    last = klines[-1]
    price = last["close"]
    high = last["high"]
    low = last["low"]
    volume = last["volume"]

    lines = [f"=== {symbol} — Timeframe {tf.upper()} ==="]
    lines.append(f"Prix: {price} | High: {high} | Low: {low} | Volume: {volume}")

    # EMA
    for p in (7, 21, 50):
        key = f"ema_{p}"
        vals = [v for v in indicators.get(key, []) if v is not None]
        if vals:
            current = vals[-1]
            prev = vals[-4] if len(vals) >= 4 else vals[0]
            trend = "hausse" if current > prev else "baisse" if current < prev else "flat"
            lines.append(f"EMA {p}: {current:.2f} ({trend})")

    # RSI
    rsi = [v for v in indicators.get("rsi", []) if v is not None]
    if rsi:
        current = rsi[-1]
        zone = "survendu" if current < 30 else "surachete" if current > 70 else "neutre"
        prev = rsi[-4] if len(rsi) >= 4 else rsi[0]
        trend = "monte" if current > prev else "descend"
        lines.append(f"RSI 14: {current:.1f} ({zone}, {trend})")

    # MACD
    macd_l = [v for v in indicators.get("macd_line", []) if v is not None]
    macd_s = [v for v in indicators.get("macd_signal", []) if v is not None]
    macd_h = [v for v in indicators.get("macd_hist", []) if v is not None]
    if macd_l and macd_s and macd_h:
        h = macd_h[-1]
        h_prev = macd_h[-2] if len(macd_h) >= 2 else 0
        momentum = "croissant" if h > h_prev else "decroissant"
        cross = "au-dessus signal" if macd_l[-1] > macd_s[-1] else "sous signal"
        lines.append(f"MACD: line={macd_l[-1]:.2f} signal={macd_s[-1]:.2f} hist={h:.2f} ({cross}, momentum {momentum})")

    # Bollinger
    bb_u = [v for v in indicators.get("bb_upper", []) if v is not None]
    bb_m = [v for v in indicators.get("bb_mid", []) if v is not None]
    bb_lo = [v for v in indicators.get("bb_lower", []) if v is not None]
    if bb_u and bb_m and bb_lo:
        p = float(price)
        band_width = bb_u[-1] - bb_lo[-1]
        position = "pres du haut" if p > bb_m[-1] + band_width * 0.3 else "pres du bas" if p < bb_m[-1] - band_width * 0.3 else "au milieu"
        lines.append(f"Bollinger: upper={bb_u[-1]:.2f} mid={bb_m[-1]:.2f} lower={bb_lo[-1]:.2f} (prix {position})")

    # Stoch RSI
    stoch_k = [v for v in indicators.get("stoch_rsi_k", []) if v is not None]
    stoch_d = [v for v in indicators.get("stoch_rsi_d", []) if v is not None]
    if stoch_k and stoch_d:
        zone = "survendu" if stoch_k[-1] < 20 else "surachete" if stoch_k[-1] > 80 else "neutre"
        lines.append(f"Stoch RSI: K={stoch_k[-1]:.1f} D={stoch_d[-1]:.1f} ({zone})")

    # ATR
    atr = [v for v in indicators.get("atr", []) if v is not None]
    if atr:
        lines.append(f"ATR 14: {atr[-1]:.2f}")

    # ADX
    adx = [v for v in indicators.get("adx", []) if v is not None]
    if adx:
        strength = "forte tendance" if adx[-1] > 25 else "faible tendance" if adx[-1] < 20 else "tendance moderee"
        lines.append(f"ADX: {adx[-1]:.1f} ({strength})")

    # MFI
    mfi = [v for v in indicators.get("mfi", []) if v is not None]
    if mfi:
        zone = "survendu" if mfi[-1] < 20 else "surachete" if mfi[-1] > 80 else "neutre"
        lines.append(f"MFI: {mfi[-1]:.1f} ({zone})")

    # OBV trend
    obv = [v for v in indicators.get("obv", []) if v is not None]
    if obv and len(obv) >= 10:
        obv_trend = "hausse" if obv[-1] > obv[-10] else "baisse"
        lines.append(f"OBV: tendance {obv_trend}")

    # Buy/sell pressure
    bs = [v for v in indicators.get("buy_sell", []) if v is not None]
    if bs:
        pressure = "achat" if bs[-1] > 2 else "vente" if bs[-1] < -2 else "equilibre"
        lines.append(f"Pression: {bs[-1]:.1f}% ({pressure})")

    # VWAP
    vwap = [v for v in indicators.get("vwap", []) if v is not None]
    if vwap:
        p = float(price)
        pos = "au-dessus" if p > vwap[-1] else "en-dessous"
        lines.append(f"VWAP: {vwap[-1]:.2f} (prix {pos})")

    # Price action structure
    lines.append(_build_price_action(klines, tf))

    return "\n".join(lines)


def _build_price_action(klines: list, tf: str) -> str:
    """Build a compact price structure summary so Claude can 'see' the chart."""
    n_candles = {"5m": 48, "15m": 36, "1h": 30, "4h": 24}.get(tf, 24)
    recent = klines[-n_candles:]
    if len(recent) < 5:
        return ""

    closes = [float(k["close"]) for k in recent]
    highs = [float(k["high"]) for k in recent]
    lows = [float(k["low"]) for k in recent]
    opens = [float(k["open"]) for k in recent]
    volumes = [float(k["volume"]) for k in recent]

    # Closes compacts (sampled to ~12 points max for readability)
    step = max(1, len(closes) // 12)
    sampled = [f"{closes[i]:.0f}" for i in range(0, len(closes), step)]
    if closes[-1] != float(sampled[-1]):
        sampled.append(f"{closes[-1]:.0f}")

    lines = [f"--- Price action ({len(recent)} bougies {tf}) ---"]
    lines.append(f"Closes: {' → '.join(sampled)}")

    # Session range
    session_high = max(highs)
    session_low = min(lows)
    range_pct = (session_high - session_low) / session_low * 100 if session_low > 0 else 0
    lines.append(f"High: {session_high:.0f} | Low: {session_low:.0f} | Range: {range_pct:.1f}%")

    # Green/red count
    green = sum(1 for o, c in zip(opens, closes) if c > o)
    lines.append(f"Bougies: {green} vertes, {len(recent) - green} rouges")

    # Swing detection (local high/low)
    swing_highs = []
    swing_lows = []
    for i in range(2, len(highs) - 2):
        if highs[i] >= highs[i-1] and highs[i] >= highs[i-2] and highs[i] >= highs[i+1] and highs[i] >= highs[i+2]:
            ago = len(highs) - 1 - i
            swing_highs.append((highs[i], ago))
        if lows[i] <= lows[i-1] and lows[i] <= lows[i-2] and lows[i] <= lows[i+1] and lows[i] <= lows[i+2]:
            ago = len(lows) - 1 - i
            swing_lows.append((lows[i], ago))

    if swing_highs:
        sh = max(swing_highs, key=lambda x: x[0])
        lines.append(f"Swing high: {sh[0]:.0f} (il y a {sh[1]} bougies)")
    if swing_lows:
        sl = min(swing_lows, key=lambda x: x[0])
        lines.append(f"Swing low: {sl[0]:.0f} (il y a {sl[1]} bougies)")

    # Significant wicks (lower wicks > 0.3% of price = rejection signals)
    wicks = []
    for i, (k, h, l, o, c) in enumerate(zip(recent, highs, lows, opens, closes)):
        body_low = min(o, c)
        body_high = max(o, c)
        lower_wick = (body_low - l) / body_low * 100 if body_low > 0 else 0
        upper_wick = (h - body_high) / body_high * 100 if body_high > 0 else 0
        ago = len(recent) - 1 - i
        if lower_wick > 0.3:
            wicks.append(f"meche basse {l:.0f} (-{lower_wick:.1f}%, {ago} bougies)")
        if upper_wick > 0.3:
            wicks.append(f"meche haute {h:.0f} (+{upper_wick:.1f}%, {ago} bougies)")
    if wicks:
        lines.append(f"Rejets: {', '.join(wicks[-4:])}")

    # Volume profile: is current volume above or below average?
    avg_vol = sum(volumes) / len(volumes) if volumes else 0
    last_vol = volumes[-1] if volumes else 0
    if avg_vol > 0:
        vol_ratio = last_vol / avg_vol
        vol_desc = "fort" if vol_ratio > 1.5 else "faible" if vol_ratio < 0.6 else "normal"
        lines.append(f"Volume actuel: {vol_desc} ({vol_ratio:.1f}x la moyenne)")

    return "\n".join(lines)


def _build_levels_section(analysis: dict) -> str:
    levels = analysis.get("key_levels", [])
    current = analysis.get("current_price", "?")
    lines = [f"\n=== NIVEAUX CLES (prix actuel: {current}) ==="]
    for lvl in levels:
        dist = lvl.get("distance_pct", "")
        dist_str = f" ({dist}%)" if dist else ""
        lines.append(f"{lvl['label']} ({lvl['type']}): {lvl['price']}{dist_str}")
    return "\n".join(lines)


def _build_orderbook_section(symbol: str) -> str:
    ob = orderbook_tracker.get_orderbook_data(symbol)
    if not ob:
        return "\n=== ORDERBOOK ===\nDonnees indisponibles"

    lines = ["\n=== ORDERBOOK ==="]
    imbalance = ob.get("imbalance")
    if imbalance is not None:
        imb = float(imbalance)
        side = "achat" if imb > 0 else "vente"
        lines.append(f"Imbalance: {imb:.1f}% (pression {side})")
    spread = ob.get("spread_pct")
    if spread is not None:
        lines.append(f"Spread: {float(spread):.4f}%")
    walls = ob.get("walls", [])
    for w in walls[:5]:
        side_label = "support" if w["side"] == "BID" else "resistance"
        lines.append(f"Mur {side_label}: {w['price']} ({w.get('pct_of_total', '?')}% du volume)")
    return "\n".join(lines)


def _build_whale_section(symbol: str) -> str:
    whales = whale_tracker.get_whale_alerts()
    symbol_whales = [w for w in whales if w["symbol"] == symbol][:10]
    if not symbol_whales:
        return "\n=== WHALE ALERTS ===\nAucun mouvement majeur recent"

    lines = ["\n=== WHALE ALERTS ==="]
    for w in symbol_whales:
        side = "ACHAT" if w["side"] == "BUY" else "VENTE"
        qty = float(w["quote_qty"])
        lines.append(f"{side} {qty:,.0f} USDC a {w['price']}")
    return "\n".join(lines)


def _build_macro_section() -> str:
    macro = macro_tracker.get_macro_data()
    indicators = macro.get("indicators", {})
    if not indicators:
        return "\n=== CONTEXTE MACRO ===\nDonnees indisponibles"

    lines = ["\n=== CONTEXTE MACRO ==="]
    names = {
        "dxy": "DXY (Dollar Index)", "vix": "VIX (Volatilite)",
        "nasdaq": "Nasdaq", "sp500": "S&P 500", "gold": "Or",
        "us10y": "Taux US 10 ans", "oil": "Petrole",
        "usdjpy": "USD/JPY", "mstr": "MicroStrategy", "ibit": "BTC ETF (IBIT)",
        "cac40": "CAC 40", "dax": "DAX", "eurusd": "EUR/USD",
    }
    impacts = {
        "dxy": "inverse", "vix": "inverse", "nasdaq": "direct",
        "sp500": "direct", "gold": "inverse", "us10y": "inverse",
        "oil": "inverse", "usdjpy": "direct", "mstr": "direct", "ibit": "direct",
        "cac40": "direct", "dax": "direct", "eurusd": "inverse",
    }
    for key in ["dxy", "vix", "nasdaq", "sp500", "gold", "us10y", "oil", "usdjpy", "mstr", "ibit", "cac40", "dax", "eurusd"]:
        ind = indicators.get(key)
        if not ind:
            continue
        name = names.get(key, key)
        change = float(ind.get("change_pct", 0) or 0)
        trend = ind.get("trend", "?")
        impact = impacts.get(key, "?")
        sign = "+" if change >= 0 else ""
        lines.append(f"{name}: {ind.get('value', '?')} ({sign}{change:.2f}%, tendance {trend}, correlation crypto {impact})")
    return "\n".join(lines)


def _build_news_section() -> str:
    news_data = news_tracker.get_news()
    items = news_data.get("items", [])
    if not items:
        return "\n=== NEWS RECENTES ===\nAucune news disponible"

    lines = ["\n=== NEWS RECENTES (les plus recentes) ==="]
    for item in items[:15]:
        title = item.get("title_fr") or item.get("title", "")
        source = item.get("source", "")
        category = item.get("category", "")
        lines.append(f"[{category}] {title} — {source}")
    return "\n".join(lines)


def _build_temporal_section() -> str:
    now_utc = datetime.now(timezone.utc)
    hour_utc = now_utc.hour
    hour_paris = (hour_utc + 1) % 24  # CET (simplifié, +2 en été)
    day = now_utc.weekday()  # 0=lundi
    day_names = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

    # Market sessions (heures UTC)
    sessions = []
    if 0 <= hour_utc < 8:
        sessions.append("session asiatique (volume faible, souvent range)")
    if 7 <= hour_utc < 16:
        sessions.append("session europeenne")
    if 13 <= hour_utc < 21:
        sessions.append("session US / Wall Street (volume max crypto)")
    if 21 <= hour_utc or hour_utc < 1:
        sessions.append("apres cloture US (volume en baisse)")

    lines = ["\n=== CONTEXTE TEMPOREL ==="]
    lines.append(f"Date/heure: {now_utc.strftime('%Y-%m-%d %H:%M')} UTC ({day_names[day]}, {hour_paris}h heure Paris)")
    lines.append(f"Sessions actives: {', '.join(sessions)}")

    if day >= 5:  # samedi/dimanche
        lines.append("WEEKEND: volume crypto significativement reduit, faux breakouts frequents, prudence sur les niveaux")
    elif day == 4 and hour_utc >= 20:
        lines.append("VENDREDI SOIR: cloture positions avant weekend, volume en baisse, expiration options crypto possibles")
    elif day == 0 and hour_utc < 8:
        lines.append("LUNDI MATIN: ouverture semaine, gap CME Bitcoin potentiel a combler, volatilite possible")
    elif day == 4 and 13 <= hour_utc < 21:
        lines.append("VENDREDI SESSION US: attention expirations options, mouvements de cloture hebdo")

    if 13 <= hour_utc < 14:
        lines.append("OUVERTURE WALL STREET: volatilite elevee attendue dans les 30-60 prochaines minutes")

    return "\n".join(lines)