from __future__ import annotations

TIMEFRAMES = ["5m", "15m", "1h", "4h"]

SIGNAL_DESCRIPTIONS = {
    "RSI": lambda v, sc: f"RSI a {v:.0f}" + (" (survente)" if sc > 0 else " (surachat)" if sc < 0 else ""),
    "MACD": lambda v, sc: "MACD croisement haussier" if sc > 0.5 else "MACD positif" if sc > 0 else "MACD croisement baissier" if sc < -0.5 else "MACD negatif" if sc < 0 else "MACD neutre",
    "MA": lambda v, sc: "prix au-dessus des MM" if sc > 0 else "prix sous les MM" if sc < 0 else "MM neutres",
    "BB": lambda v, sc: f"Bollinger bande basse (position {v:.0%})" if sc > 0 else f"Bollinger bande haute (position {v:.0%})" if sc < 0 else "Bollinger neutre",
    "MFI": lambda v, sc: f"MFI a {v:.0f}" + (" (survente)" if sc > 0 else " (surachat)" if sc < 0 else ""),
    "StochRSI": lambda v, sc: "StochRSI croisement haussier en survente" if sc > 0.5 else "StochRSI en survente" if sc > 0 else "StochRSI croisement baissier en surachat" if sc < -0.5 else "StochRSI en surachat" if sc < 0 else "StochRSI neutre",
    "B/S": lambda v, sc: f"pression acheteuse ({v:+.0f}%)" if sc > 0 else f"pression vendeuse ({v:+.0f}%)" if sc < 0 else "pression neutre",
    "OBV": lambda v, sc: "OBV divergence haussiere" if sc > 0.5 else "OBV confirme la hausse" if sc > 0 else "OBV divergence baissiere" if sc < -0.5 else "OBV confirme la baisse" if sc < 0 else "OBV neutre",
    "OB_Imbalance": lambda v, sc: f"carnet d'ordres desequilibre {'acheteur' if sc > 0 else 'vendeur'} ({v:+.1%})" if abs(sc) > 0.1 else "carnet d'ordres equilibre",
    "Rejection": lambda v, sc: f"rejet haussier (meche {v:.1f}x corps)" if sc > 0 else f"rejet baissier (meche {v:.1f}x corps)",
    "LevelTest": lambda v, sc: f"{int(v)} tests du support valides" if sc > 0 else f"{int(v)} tests de la resistance valides",
    "Retest": lambda v, sc: "break-and-retest haussier" if sc > 0 else "break-and-retest baissier",
}

MACRO_DESC = {
    "DXY": lambda s: f"DXY en {'hausse (bearish crypto)' if s['score'] < 0 else 'baisse (bullish crypto)' if s['score'] > 0 else 'neutre'}",
    "VIX": lambda s: f"VIX a {float(s.get('value', 0)):.0f}" + (" (risk-off, prudence)" if s["score"] < -0.3 else " (risk-on, favorable)" if s["score"] > 0.3 else " (neutre)"),
    "Nasdaq": lambda s: f"Nasdaq en {'hausse (risk-on)' if s['score'] > 0 else 'baisse (risk-off)' if s['score'] < 0 else 'neutre'}",
    "Gold": lambda s: f"Or en {'hausse (risk-off, fuite vers valeur refuge)' if s.get('trend') == 'up' else 'baisse (risk-on)' if s.get('trend') == 'down' else 'neutre'}",
    "US10Y": lambda s: f"taux 10Y en {'hausse (pression liquidite)' if s['score'] < 0 else 'baisse (assouplissement)' if s['score'] > 0 else 'neutre'}",
    "Spread": lambda s: "courbe des taux inversee (signal recession)" if float(s.get("value", 0)) < 0 else "courbe des taux normale",
    "Oil": lambda s: f"petrole en {'hausse (pression inflation)' if s['score'] < 0 else 'baisse (desinflation)' if s['score'] > 0 else 'neutre'}",
    "USD/JPY": lambda s: f"yen en {'hausse (carry trade unwind, risk-off)' if s['score'] < 0 else 'baisse (risk-on)' if s['score'] > 0 else 'neutre'}",
}


def signal_to_dict(s: dict) -> dict:
    score = s["score"]
    if score > 0.15:
        signal = "bullish"
    elif score < -0.15:
        signal = "bearish"
    else:
        signal = "neutral"
    return {
        "name": s["name"],
        "value": s.get("value"),
        "signal": signal,
        "score": round(score, 2),
        "trend": s.get("trend"),
    }


def format_qty(qty_str: str) -> str:
    try:
        val = float(qty_str)
        if val >= 1_000_000:
            return f"{val / 1_000_000:.1f}M"
        if val >= 1_000:
            return f"{val / 1_000:.0f}K"
        return qty_str
    except (ValueError, TypeError):
        return qty_str
