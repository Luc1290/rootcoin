import json
from datetime import datetime, timezone

import structlog

from backend.core.config import settings
from backend.market import kline_manager, macro_tracker, market_analyzer, orderbook_tracker, whale_tracker
from backend.services import news_tracker

log = structlog.get_logger()

_last_analysis: dict | None = None

SYSTEM_PROMPT = """Tu es un analyste de trading intraday expert specialise en crypto sur Binance margin cross x5.
Capital : ~10 000 USDC. Horizon : scalping / intraday.

REGLES STRICTES :
- Tu DOIS choisir une direction : LONG ou SHORT. Jamais FLAT, jamais "attendre".
- Tes niveaux (entry, SL, TP) doivent etre precis au dollar pres.
- Ton explication doit justifier ta decision avec les donnees fournies (indicateurs, niveaux, orderbook, macro, news).
- Reponds UNIQUEMENT en JSON valide, sans markdown, sans commentaire autour.

Format JSON attendu :
{
  "direction": "LONG ou SHORT",
  "entry": 00000.00,
  "stop_loss": 00000.00,
  "tp1": 00000.00,
  "tp2": 00000.00,
  "risk_reward": 0.0,
  "confidence": "faible ou moderee ou elevee",
  "explanation": "3-5 phrases justifiant ta decision avec les donnees",
  "key_signal": "Le signal principal en 1 phrase",
  "invalidation": "Ce qui invaliderait ce trade en 1 phrase"
}"""

INDICATORS_SET = {"ema", "rsi", "macd", "bb", "obv", "stoch_rsi", "atr", "adx", "mfi", "buy_sell", "vwap"}
TIMEFRAMES = ["5m", "15m", "1h", "4h"]


def get_last_analysis() -> dict | None:
    return _last_analysis


async def analyze(symbol: str) -> dict:
    global _last_analysis

    api_key = settings.anthropic_api_key.get_secret_value()
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY non configuree dans .env"}

    prompt = await build_prompt(symbol)

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
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
        _last_analysis = result
        log.info("llm_analysis_done", symbol=symbol, direction=result.get("direction"),
                 tokens_in=message.usage.input_tokens, tokens_out=message.usage.output_tokens)
        return result
    except Exception as e:
        log.error("llm_analysis_failed", symbol=symbol, error=str(e), exc_info=True)
        return {"error": str(e), "symbol": symbol}


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

    sections.append(f"\nAnalyse {symbol} et donne ta recommandation de trade.")

    return "\n".join(sections)


async def _build_tf_section(symbol: str, tf: str) -> str | None:
    limit = {"5m": 100, "15m": 80, "1h": 60, "4h": 45}.get(tf, 60)
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

    # Recent price action (last 10 candles summarized)
    recent = klines[-10:]
    opens = [float(k["open"]) for k in recent]
    closes_r = [float(k["close"]) for k in recent]
    green = sum(1 for o, c in zip(opens, closes_r) if c > o)
    lines.append(f"10 dernieres bougies: {green} vertes, {10 - green} rouges")

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
    }
    impacts = {
        "dxy": "inverse", "vix": "inverse", "nasdaq": "direct",
        "sp500": "direct", "gold": "inverse", "us10y": "inverse",
        "oil": "inverse", "usdjpy": "direct", "mstr": "direct", "ibit": "direct",
    }
    for key in ["dxy", "vix", "nasdaq", "sp500", "gold", "us10y", "oil", "usdjpy", "mstr", "ibit"]:
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
