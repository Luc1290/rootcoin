import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import structlog

from backend.core.config import settings
from backend.core.database import async_session
from backend.core.models import Setting

log = structlog.get_logger()

_BASE_URL = "https://api.telegram.org/bot{token}"
_enabled: bool = False
_notify_positions: bool = True
_notify_orders: bool = True
_notify_levels: bool = True
_notify_pnl: bool = True
_notify_heatmap: bool = True
_notify_momentum: bool = True
_http: httpx.AsyncClient | None = None
_summary_task: asyncio.Task | None = None
_callback_task: asyncio.Task | None = None
_callback_offset: int = 0
SUMMARY_INTERVAL = 4 * 3600  # 4 heures

SETTING_KEY = "telegram_enabled"
SETTING_POSITIONS = "notify_positions"
SETTING_ORDERS = "notify_orders"
SETTING_LEVELS = "notify_levels"
SETTING_PNL = "notify_pnl"
SETTING_HEATMAP = "notify_heatmap"
SETTING_MOMENTUM = "notify_momentum"
_CATEGORY_KEYS = {SETTING_POSITIONS, SETTING_ORDERS, SETTING_LEVELS, SETTING_PNL, SETTING_HEATMAP, SETTING_MOMENTUM}


async def start():
    global _enabled, _notify_positions, _notify_orders, _notify_levels, _notify_pnl, _notify_heatmap, _notify_momentum, _http, _summary_task, _callback_task
    _http = httpx.AsyncClient(timeout=10)
    _enabled = await _load_setting(SETTING_KEY, is_configured())
    _notify_positions = await _load_setting(SETTING_POSITIONS, True)
    _notify_orders = await _load_setting(SETTING_ORDERS, True)
    _notify_levels = await _load_setting(SETTING_LEVELS, True)
    _notify_pnl = await _load_setting(SETTING_PNL, True)
    _notify_heatmap = await _load_setting(SETTING_HEATMAP, True)
    _notify_momentum = await _load_setting(SETTING_MOMENTUM, True)
    _summary_task = asyncio.create_task(_periodic_summary_loop())
    _callback_task = asyncio.create_task(_callback_polling_loop())
    log.info("telegram_notifier_started", configured=is_configured(), enabled=_enabled)


async def stop():
    global _http, _summary_task, _callback_task
    if _summary_task:
        _summary_task.cancel()
        _summary_task = None
    if _callback_task:
        _callback_task.cancel()
        _callback_task = None
    if _http:
        await _http.aclose()
        _http = None
    log.info("telegram_notifier_stopped")


# ── State queries ────────────────────────────────────────────


def is_configured() -> bool:
    return bool(
        settings.telegram_bot_token.get_secret_value()
        and settings.telegram_chat_id.get_secret_value()
    )


def is_enabled() -> bool:
    return _enabled and is_configured()


def is_positions_enabled() -> bool:
    return is_enabled() and _notify_positions


def is_orders_enabled() -> bool:
    return is_enabled() and _notify_orders


def is_levels_enabled() -> bool:
    return is_enabled() and _notify_levels


def is_pnl_enabled() -> bool:
    return is_enabled() and _notify_pnl


def is_heatmap_enabled() -> bool:
    return is_enabled() and _notify_heatmap


def is_momentum_enabled() -> bool:
    return is_enabled() and _notify_momentum


def get_categories() -> dict:
    return {
        SETTING_POSITIONS: _notify_positions,
        SETTING_ORDERS: _notify_orders,
        SETTING_LEVELS: _notify_levels,
        SETTING_PNL: _notify_pnl,
        SETTING_HEATMAP: _notify_heatmap,
        SETTING_MOMENTUM: _notify_momentum,
    }


# ── State mutations ──────────────────────────────────────────


async def set_enabled(enabled: bool):
    global _enabled
    _enabled = enabled
    await _save_setting(SETTING_KEY, enabled)
    log.info("telegram_enabled_changed", enabled=enabled)


async def set_category_enabled(key: str, enabled: bool):
    global _notify_positions, _notify_orders, _notify_levels, _notify_pnl, _notify_heatmap, _notify_momentum
    if key not in _CATEGORY_KEYS:
        return
    if key == SETTING_POSITIONS:
        _notify_positions = enabled
    elif key == SETTING_ORDERS:
        _notify_orders = enabled
    elif key == SETTING_LEVELS:
        _notify_levels = enabled
    elif key == SETTING_PNL:
        _notify_pnl = enabled
    elif key == SETTING_HEATMAP:
        _notify_heatmap = enabled
    elif key == SETTING_MOMENTUM:
        _notify_momentum = enabled
    await _save_setting(key, enabled)
    log.info("telegram_category_changed", key=key, enabled=enabled)


# ── Core send ────────────────────────────────────────────────


async def notify(message: str, parse_mode: str = "HTML", retries: int = 0) -> bool:
    if not is_enabled() or not _http:
        return False
    url = f"{_BASE_URL.format(token=settings.telegram_bot_token.get_secret_value())}/sendMessage"
    for attempt in range(1 + retries):
        try:
            resp = await _http.post(url, json={
                "chat_id": settings.telegram_chat_id.get_secret_value(),
                "text": message,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            })
            if resp.status_code == 200:
                return True
            if resp.status_code == 429 and attempt < retries:
                retry_after = int(resp.json().get("parameters", {}).get("retry_after", 2))
                await asyncio.sleep(retry_after)
                continue
            log.warning("telegram_send_failed", status=resp.status_code, body=resp.text[:200])
            return False
        except Exception as e:
            log.warning("telegram_send_error", error=type(e).__name__)
            return False
    return False


async def test_connection() -> bool:
    if not is_configured() or not _http:
        return False
    try:
        url = f"{_BASE_URL.format(token=settings.telegram_bot_token.get_secret_value())}/sendMessage"
        resp = await _http.post(url, json={
            "chat_id": settings.telegram_chat_id.get_secret_value(),
            "text": "\u2705 RootCoin \u2014 Connexion Telegram OK !",
            "parse_mode": "HTML",
        })
        return resp.status_code == 200
    except Exception:
        log.error("telegram_test_error", exc_info=True)
        return False


# ── Position notifications ───────────────────────────────────


async def notify_position_opened(
    symbol: str, side: str, price: Decimal, qty: Decimal, market_type: str,
):
    if not is_positions_enabled():
        return
    base = symbol.replace("USDC", "").replace("USDT", "")
    mtype = market_type.replace("_", " ").title()
    usd_val = float(price * qty)
    msg = (
        f"\U0001f7e2 <b>{symbol} {side} ouvert</b>\n"
        f"\n"
        f"\U0001f4b0 {_fq(qty)} {base} @ {_fp(price)}\n"
        f"\U0001f4b5 Valeur : ${usd_val:,.0f}\n"
        f"\U0001f3e6 {mtype}"
    )
    await notify(msg)


async def notify_position_closed(
    symbol: str, side: str, entry_price: Decimal, exit_price: Decimal,
    realized_pnl: Decimal, net_pnl: Decimal, pnl_pct: Decimal,
    opened_at: datetime | None, closed_at: datetime | None,
    exit_reason: str = "",
):
    if not is_positions_enabled():
        log.warning("close_notif_skipped", symbol=symbol, enabled=is_enabled(),
                     positions_cat=_notify_positions)
        return
    log.info("close_notif_sending", symbol=symbol, side=side, reason=exit_reason)
    win = net_pnl > 0
    if exit_reason == "SL":
        header = f"\u26d4 <b>{symbol} {side} — Stop Loss touche</b>"
    elif exit_reason == "TP":
        header = f"\U0001f3af <b>{symbol} {side} — Take Profit atteint !</b>"
    elif exit_reason == "OCO":
        if win:
            header = f"\U0001f3af <b>{symbol} {side} — Take Profit atteint !</b>"
        else:
            header = f"\u26d4 <b>{symbol} {side} — Stop Loss touche</b>"
    else:
        icon = "\u2705" if win else "\u274c"
        header = f"{icon} <b>{symbol} {side} — Position fermee</b>"
    sign = "+" if net_pnl > 0 else ""
    sign_g = "+" if realized_pnl > 0 else ""
    duration = _fmt_duration(opened_at, closed_at)
    fees = realized_pnl - net_pnl
    capital = await _get_capital()
    cap_str = _cap_pct(float(net_pnl), capital)
    msg = (
        f"{header}\n"
        f"\n"
        f"\U0001f4c8 Entr\u00e9e {_fp(entry_price)}  \u2192  Sortie {_fp(exit_price)}\n"
        f"\U0001f4b0 PnL brut : {sign_g}{_fp(realized_pnl)}\n"
        f"\U0001f4b8 Frais : -{_fp(abs(fees))}\n"
        f"{'=' * 20}\n"
        f"<b>{'✅' if win else '❌'} PnL net : {sign}{_fp(net_pnl)}{cap_str}</b>\n"
        f"\u23f1 Duree : {duration}"
    )
    ok = await notify(msg, retries=2)
    if not ok:
        log.warning("close_notif_failed", symbol=symbol, side=side)


async def notify_position_dca(
    symbol: str, side: str, price: Decimal, qty: Decimal,
    new_avg: Decimal, new_total: Decimal,
):
    if not is_positions_enabled():
        return
    base = symbol.replace("USDC", "").replace("USDT", "")
    msg = (
        f"\U0001f504 <b>{symbol} {side}</b> DCA\n"
        f"+{_fq(qty)} {base} @ {_fp(price)}\n"
        f"Moy {_fp(new_avg)}  \u2022  Total {_fq(new_total)} {base}"
    )
    await notify(msg)


async def notify_position_opened_batch(
    symbol: str, side: str, avg_price: Decimal, total_qty: Decimal,
    market_type: str, num_fills: int,
):
    if not is_positions_enabled():
        return
    base = symbol.replace("USDC", "").replace("USDT", "")
    mtype = market_type.replace("_", " ").title()
    usd_val = float(avg_price * total_qty)
    msg = (
        f"\U0001f7e2 <b>{symbol} {side} ouvert</b>\n"
        f"\n"
        f"\U0001f4b0 {_fq(total_qty)} {base} @ {_fp(avg_price)}\n"
        f"\U0001f4b5 Valeur : ${usd_val:,.0f}  \u2022  {num_fills} fills\n"
        f"\U0001f3e6 {mtype}"
    )
    await notify(msg)


async def notify_position_dca_batch(
    symbol: str, side: str, avg_price: Decimal, added_qty: Decimal,
    new_total: Decimal, num_fills: int,
):
    if not is_positions_enabled():
        return
    base = symbol.replace("USDC", "").replace("USDT", "")
    msg = (
        f"\U0001f504 <b>{symbol} {side}</b> DCA\n"
        f"+{_fq(added_qty)} {base} ({num_fills} fills)\n"
        f"Moy {_fp(avg_price)}  \u2022  Total {_fq(new_total)} {base}"
    )
    await notify(msg)


async def notify_position_reduced(
    symbol: str, side: str, price: Decimal, qty: Decimal,
    remaining: Decimal, realized: Decimal,
):
    if not is_positions_enabled():
        return
    base = symbol.replace("USDC", "").replace("USDT", "")
    sign = "+" if realized > 0 else ""
    msg = (
        f"\U0001f4c9 <b>{symbol} {side} — Reduction</b>\n"
        f"\n"
        f"Vendu : {_fq(qty)} {base} @ {_fp(price)}\n"
        f"PnL : {sign}{_fp(realized)}\n"
        f"Restant : {_fq(remaining)} {base}"
    )
    await notify(msg)


async def notify_position_closed_reconciled(
    symbol: str, side: str, entry_price: Decimal,
    exit_price: Decimal | None, net_pnl: Decimal | None,
    pnl_pct: Decimal | None,
):
    if not is_positions_enabled():
        log.warning("close_reconciled_notif_skipped", symbol=symbol, enabled=is_enabled(),
                     positions_cat=_notify_positions)
        return
    log.info("close_reconciled_notif_sending", symbol=symbol, side=side)
    lines = [f"\U0001f534 <b>{symbol} {side} — Ferme (reconciliation)</b>", ""]
    if exit_price and exit_price > 0:
        lines.append(f"Entr\u00e9e {_fp(entry_price)}  \u2192  Sortie {_fp(exit_price)}")
    else:
        lines.append(f"Entr\u00e9e {_fp(entry_price)}")
    if net_pnl is not None:
        sign = "+" if net_pnl > 0 else ""
        icon = "\u2705" if net_pnl > 0 else "\u274c"
        pct_str = f" ({sign}{_fpct(pnl_pct)}%)" if pnl_pct is not None else ""
        lines.append(f"{icon} <b>PnL net : {sign}{_fp(net_pnl)}{pct_str}</b>")
    await notify("\n".join(lines), retries=2)


# ── Order notifications ──────────────────────────────────────


async def notify_sl_placed(
    symbol: str, side: str, stop_price: Decimal, qty: Decimal, entry_price: Decimal,
):
    if not is_orders_enabled():
        return
    dist_pct = _pct_distance(side, entry_price, stop_price)
    pnl = _pnl_usd_str(side, entry_price, stop_price, qty)
    base = symbol.replace("USDC", "").replace("USDT", "")
    msg = (
        f"\u26d4 <b>{symbol} — Stop Loss place</b>\n"
        f"\n"
        f"Stop : {_fp(stop_price)} ({dist_pct} \u2192 {pnl})\n"
        f"Qt\u00e9 : {_fq(qty)} {base}"
    )
    await notify(msg)


async def notify_tp_placed(
    symbol: str, side: str, tp_price: Decimal, qty: Decimal, entry_price: Decimal,
):
    if not is_orders_enabled():
        return
    dist_pct = _pct_distance(side, entry_price, tp_price)
    pnl = _pnl_usd_str(side, entry_price, tp_price, qty)
    base = symbol.replace("USDC", "").replace("USDT", "")
    msg = (
        f"\U0001f3af <b>{symbol} — Take Profit place</b>\n"
        f"\n"
        f"Target : {_fp(tp_price)} ({dist_pct} \u2192 {pnl})\n"
        f"Qt\u00e9 : {_fq(qty)} {base}"
    )
    await notify(msg)


async def notify_oco_placed(
    symbol: str, side: str, tp_price: Decimal, sl_price: Decimal,
    qty: Decimal, entry_price: Decimal,
):
    if not is_orders_enabled():
        return
    tp_dist = _pct_distance(side, entry_price, tp_price)
    sl_dist = _pct_distance(side, entry_price, sl_price)
    tp_pnl = _pnl_usd_str(side, entry_price, tp_price, qty)
    sl_pnl = _pnl_usd_str(side, entry_price, sl_price, qty)
    msg = (
        f"\U0001f500 <b>{symbol} — OCO place</b>\n"
        f"\n"
        f"\U0001f3af TP : {_fp(tp_price)} ({tp_dist} \u2192 {tp_pnl})\n"
        f"\u26d4 SL : {_fp(sl_price)} ({sl_dist} \u2192 {sl_pnl})"
    )
    await notify(msg)


async def notify_position_secured(
    symbol: str, side: str, half_qty: Decimal, sl_price: Decimal, remaining: Decimal,
):
    if not is_orders_enabled():
        return
    base = symbol.replace("USDC", "").replace("USDT", "")
    msg = (
        f"\U0001f6e1\ufe0f <b>{symbol} {side} — Securise</b>\n"
        f"\n"
        f"Vendu {_fq(half_qty)} {base} au march\u00e9\n"
        f"Restant {_fq(remaining)} {base} avec SL \u00e0 {_fp(sl_price)}"
    )
    await notify(msg)


async def notify_trailing_moved(
    symbol: str, side: str, sl_price: Decimal, tp_price: Decimal,
    entry_price: Decimal, current_price: Decimal, quantity: Decimal,
    gain_pct: Decimal, is_breakeven: bool,
):
    if not is_orders_enabled():
        return
    sl_dist = _pct_distance(side, entry_price, sl_price)
    tp_dist = _pct_distance(side, entry_price, tp_price)
    gross = float((current_price - entry_price) * quantity)
    if side == "SHORT":
        gross = -gross
    fees = float((entry_price + current_price) * quantity) * 0.001
    pnl_usd = gross - fees
    sl_pnl_str = _pnl_usd_str(side, entry_price, sl_price, quantity)
    tp_pnl_str = _pnl_usd_str(side, entry_price, tp_price, quantity)
    value_usd = float(current_price * quantity)
    entry_cost = float(entry_price * quantity)
    pnl_pct = (pnl_usd / entry_cost * 100) if entry_cost > 0 else 0
    sign = "+" if gain_pct >= 0 else ""
    pnl_sign = "+" if pnl_usd >= 0 else ""
    pct_sign = "+" if pnl_pct >= 0 else ""
    base = symbol.replace("USDC", "").replace("USDT", "")
    capital = await _get_capital()
    cap_str = _cap_pct(pnl_usd, capital)
    if is_breakeven:
        header = f"\U0001f6e1\ufe0f <b>{symbol} {side} — SL prot\u00e8ge les frais</b>"
        detail = "Position sans risque.\n"
    else:
        header = f"\u2b06\ufe0f <b>{symbol} {side} — Trailing {sign}{float(gain_pct):.2f}%</b>"
        detail = ""
    msg = (
        f"{header}\n"
        f"\n"
        f"{detail}"
        f"\U0001f4b0 PnL : {pnl_sign}${pnl_usd:,.2f} ({pct_sign}{pnl_pct:.2f}%{cap_str})\n"
        f"\U0001f4c8 Entr\u00e9e {_fp(entry_price)} \u2192 Actuel {_fp(current_price)}\n"
        f"\U0001f4b5 Valeur : ${value_usd:,.2f} ({_fq(quantity)} {base})\n"
        f"\n"
        f"\U0001f3af TP : {_fp(tp_price)} ({tp_dist} \u2192 {tp_pnl_str})\n"
        f"\u26d4 SL : {_fp(sl_price)} ({sl_dist} \u2192 {sl_pnl_str})"
    )
    await notify(msg)


# ── PnL threshold notifications ───────────────────────────────


async def notify_pnl_threshold(
    symbol: str, side: str, pnl_pct: Decimal, pnl_usd: Decimal,
    entry_price: Decimal, current_price: Decimal, threshold: float,
):
    if not is_pnl_enabled():
        return
    sign = "+" if pnl_usd > 0 else ""
    if threshold == 0.0:
        header = f"\u2696\ufe0f <b>{symbol} {side} — Prix de retour \u00e0 l'entr\u00e9e</b>"
    elif threshold > 0:
        header = f"\U0001f4c8 <b>{symbol} {side} — +{threshold:g}% de gain</b>"
    else:
        header = f"\U0001f4c9 <b>{symbol} {side} — {threshold:g}% de perte</b>"
    capital = await _get_capital()
    cap_str = _cap_pct(float(pnl_usd), capital)
    msg = (
        f"{header}\n"
        f"\n"
        f"\U0001f4b0 PnL : {sign}{_fp(pnl_usd)} ({sign}{_fpct(pnl_pct)}%{cap_str})\n"
        f"\U0001f4c8 Entr\u00e9e {_fp(entry_price)}  \u2192  Actuel {_fp(current_price)}"
    )
    await notify(msg)


async def notify_pnl_usd_threshold(
    symbol: str, side: str, pnl_pct: Decimal, pnl_usd: Decimal,
    entry_price: Decimal, current_price: Decimal, quantity: Decimal,
    threshold: float,
):
    if not is_pnl_enabled():
        return
    sign = "+" if pnl_usd > 0 else ""
    pct_sign = "+" if pnl_pct > 0 else ""
    base = symbol.replace("USDC", "").replace("USDT", "")
    value_usd = float(current_price * quantity)
    if threshold >= 500:
        header = f"\U0001f4b0 <b>{symbol} {side} — {sign}${float(pnl_usd):,.0f}</b>"
        advice = "\U0001f6e1\ufe0f Pense a securiser ta position."
    elif threshold > 0:
        header = f"\U0001f4b0 <b>{symbol} {side} — {sign}${float(pnl_usd):,.0f}</b>"
        advice = ""
    else:
        header = f"\U0001f6a8 <b>{symbol} {side} — {sign}${float(pnl_usd):,.0f}</b>"
        advice = "\u26a0\ufe0f Surveille ta position." if threshold <= -500 else ""
    msg = (
        f"{header}\n"
        f"\n"
        f"\U0001f4b0 PnL : {sign}${float(pnl_usd):,.2f} ({pct_sign}{float(pnl_pct):.2f}%)\n"
        f"\U0001f4c8 Entree {_fp(entry_price)} \u2192 Actuel {_fp(current_price)}\n"
        f"\U0001f4b5 Valeur : ${value_usd:,.2f} ({_fq(quantity)} {base})"
    )
    if advice:
        msg += f"\n\n{advice}"
    await notify(msg)


# ── Startup notification ──────────────────────────────────────


async def notify_startup_summary():
    if not is_enabled():
        return
    from backend.trading import position_tracker, pnl
    positions = position_tracker.get_positions()
    if not positions:
        await notify("\U0001f680 <b>RootCoin demarre</b> \u2014 Aucune position active.")
        return
    total_pnl = Decimal("0")
    lines = [f"\U0001f680 <b>RootCoin demarre</b>", ""]
    for pos in positions:
        unrealized = Decimal("0")
        u_pct = Decimal("0")
        if pos.current_price and pos.current_price > 0:
            unrealized, u_pct = pnl.unrealized_pnl(
                pos.side, pos.entry_price, pos.current_price,
                pos.quantity, pos.entry_fees_usd,
            )
        total_pnl += unrealized
        sign = "+" if unrealized > 0 else ""
        icon = "\U0001f7e2" if unrealized >= 0 else "\U0001f534"
        base = pos.symbol.replace("USDC", "").replace("USDT", "")
        lines.append(
            f"{icon} <b>{pos.symbol}</b> {pos.side}\n"
            f"    {_fq(pos.quantity)} {base} @ {_fp(pos.entry_price)}\n"
            f"    PnL : {sign}{_fp(unrealized)} ({sign}{_fpct(u_pct)}%)"
        )
    t_sign = "+" if total_pnl > 0 else ""
    lines.append(f"\n<b>Total : {t_sign}{_fp(total_pnl)}</b>")
    await notify("\n".join(lines))


# ── Periodic summary ─────────────────────────────────────────


async def _periodic_summary_loop():
    try:
        while True:
            await asyncio.sleep(SUMMARY_INTERVAL)
            await _send_periodic_summary()
    except asyncio.CancelledError:
        pass


async def _send_periodic_summary():
    if not is_positions_enabled():
        return
    from backend.trading import position_tracker, pnl
    positions = position_tracker.get_positions()
    if not positions:
        return
    total_pnl = Decimal("0")
    lines = [f"\U0001f4ca <b>Resume positions</b>", ""]
    for pos in positions:
        unrealized = Decimal("0")
        u_pct = Decimal("0")
        if pos.current_price and pos.current_price > 0:
            unrealized, u_pct = pnl.unrealized_pnl(
                pos.side, pos.entry_price, pos.current_price,
                pos.quantity, pos.entry_fees_usd,
            )
        total_pnl += unrealized
        sign = "+" if unrealized > 0 else ""
        icon = "\U0001f7e2" if unrealized >= 0 else "\U0001f534"
        base = pos.symbol.replace("USDC", "").replace("USDT", "")
        lines.append(
            f"{icon} <b>{pos.symbol}</b> {pos.side}\n"
            f"    {_fp(pos.entry_price)}  \u2192  {_fp(pos.current_price or Decimal('0'))}\n"
            f"    PnL : {sign}{_fp(unrealized)} ({sign}{_fpct(u_pct)}%)"
        )
    t_sign = "+" if total_pnl > 0 else ""
    lines.append(f"\n<b>Total : {t_sign}{_fp(total_pnl)}</b>")
    await notify("\n".join(lines))


# ── Level notifications ───────────────────────────────────────


async def notify_price_alert(
    symbol: str, price: Decimal, target_price: Decimal, direction: str, note: str | None,
):
    if not is_levels_enabled():
        return
    arrow = "\u2b06\ufe0f" if direction == "above" else "\u2b07\ufe0f"
    label = "au-dessus" if direction == "above" else "en-dessous"
    lines = [
        f"{arrow} <b>{symbol} — Alerte prix</b>",
        "",
        f"Prix actuel : {_fp(price)} ({label} de {_fp(target_price)})",
    ]
    if note:
        lines.append(f"\U0001f4dd {note}")
    await notify("\n".join(lines))


async def notify_level_reached(
    symbol: str, price: Decimal, level_price: str, level_type: str, label: str,
):
    if not is_levels_enabled():
        return
    base = symbol.replace("USDC", "").replace("USDT", "")
    lp = Decimal(level_price)
    msg = (
        f"\U0001f4cd <b>{base} — {label}</b>\n"
        f"\n"
        f"Prix actuel : {_fp(price)} (niveau {_fp(lp)})"
    )
    await notify(msg)


# ── Pending order notifications (manual mode) ────────────────


async def notify_pending_oco(
    symbol: str, side: str, tp_price, sl_price,
    qty, entry_price, pos_id: int,
) -> int | None:
    """Send OCO proposal with inline Confirm/Reject buttons. Returns message_id."""
    if not is_orders_enabled() or not _http:
        return None
    tp_dist = _pct_distance(side, entry_price, tp_price)
    sl_dist = _pct_distance(side, entry_price, sl_price)
    tp_pnl = _pnl_usd_str(side, entry_price, tp_price, qty)
    sl_pnl = _pnl_usd_str(side, entry_price, sl_price, qty)
    msg = (
        f"\u23f3 <b>{symbol} {side} — OCO en attente</b>\n"
        f"\n"
        f"\U0001f3af TP : {_fp(tp_price)} ({tp_dist} \u2192 {tp_pnl})\n"
        f"\u26d4 SL : {_fp(sl_price)} ({sl_dist} \u2192 {sl_pnl})\n"
        f"\n"
        f"\u23f1 Timeout : 2 min"
    )
    keyboard = {
        "inline_keyboard": [[
            {"text": "\u2705 Confirmer", "callback_data": f"trailing_confirm:{pos_id}"},
            {"text": "\u274c Refuser", "callback_data": f"trailing_reject:{pos_id}"},
        ]]
    }
    return await _send_with_buttons(msg, keyboard)


async def edit_pending_status(message_id: int | None, status: str, source: str = ""):
    """Edit a pending notification to show final status."""
    if not message_id or not _http:
        return
    if status == "confirmed":
        icon = "\u2705"
        label = "Confirme"
        if source == "timeout":
            label = "Confirme (timeout 2min)"
        elif source == "mode_switch":
            label = "Confirme (passage auto)"
        elif source == "telegram":
            label = "Confirme via Telegram"
        else:
            label = "Confirme via dashboard"
    elif status == "rejected":
        icon = "\u274c"
        label = "Refuse"
    else:
        return

    url = f"{_BASE_URL.format(token=settings.telegram_bot_token.get_secret_value())}/editMessageReplyMarkup"
    try:
        await _http.post(url, json={
            "chat_id": settings.telegram_chat_id.get_secret_value(),
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": [[
                {"text": f"{icon} {label}", "callback_data": "noop"},
            ]]},
        })
    except Exception:
        log.warning("telegram_edit_pending_failed", message_id=message_id)


async def _send_with_buttons(message: str, reply_markup: dict) -> int | None:
    """Send message with inline keyboard, return message_id."""
    if not is_enabled() or not _http:
        return None
    url = f"{_BASE_URL.format(token=settings.telegram_bot_token.get_secret_value())}/sendMessage"
    try:
        resp = await _http.post(url, json={
            "chat_id": settings.telegram_chat_id.get_secret_value(),
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": reply_markup,
        })
        if resp.status_code == 200:
            data = resp.json()
            return data.get("result", {}).get("message_id")
        log.warning("telegram_send_buttons_failed", status=resp.status_code)
    except Exception:
        log.warning("telegram_send_buttons_error", exc_info=True)
    return None


# ── Callback polling (for inline button responses) ───────────


async def _callback_polling_loop():
    """Long-poll Telegram for callback queries from inline buttons."""
    global _callback_offset
    while True:
        try:
            if not is_configured() or not _http:
                await asyncio.sleep(10)
                continue
            url = f"{_BASE_URL.format(token=settings.telegram_bot_token.get_secret_value())}/getUpdates"
            resp = await _http.post(url, json={
                "offset": _callback_offset,
                "timeout": 30,
                "allowed_updates": ["callback_query"],
            }, timeout=40)
            if resp.status_code != 200:
                await asyncio.sleep(5)
                continue
            data = resp.json()
            for update in data.get("result", []):
                _callback_offset = update["update_id"] + 1
                cb = update.get("callback_query")
                if cb:
                    asyncio.create_task(_handle_callback(cb))
        except asyncio.CancelledError:
            break
        except Exception:
            log.warning("telegram_callback_poll_error", exc_info=True)
            await asyncio.sleep(5)


async def _handle_callback(callback_query: dict):
    """Handle inline button press from Telegram."""
    data = callback_query.get("data", "")
    callback_id = callback_query.get("id")

    if data.startswith("trailing_confirm:"):
        pos_id = int(data.split(":")[1])
        from backend.trading import trailing_manager
        ok = await trailing_manager.confirm_pending(pos_id, source="telegram")
        text = "\u2705 Ordres places !" if ok else "\u26a0\ufe0f Aucun ordre en attente"
        await _answer_callback(callback_id, text)
    elif data.startswith("trailing_reject:"):
        pos_id = int(data.split(":")[1])
        from backend.trading import trailing_manager
        ok = await trailing_manager.reject_pending(pos_id)
        text = "\u274c Refuse" if ok else "\u26a0\ufe0f Aucun ordre en attente"
        await _answer_callback(callback_id, text)
    elif data == "noop":
        await _answer_callback(callback_id, "")
    else:
        await _answer_callback(callback_id, "")


async def _answer_callback(callback_id: str, text: str):
    """Acknowledge a callback query."""
    if not _http:
        return
    url = f"{_BASE_URL.format(token=settings.telegram_bot_token.get_secret_value())}/answerCallbackQuery"
    try:
        await _http.post(url, json={
            "callback_query_id": callback_id,
            "text": text,
        })
    except Exception:
        pass


# ── Helpers ──────────────────────────────────────────────────


async def _get_capital() -> Decimal:
    from backend.core.models import Balance
    from sqlalchemy import func, select
    try:
        async with async_session() as session:
            latest_sub = select(func.max(Balance.snapshot_at)).scalar_subquery()
            result = await session.execute(
                select(func.sum(Balance.usd_value).label("total"))
                .where(Balance.snapshot_at == latest_sub, Balance.usd_value.isnot(None))
            )
            return result.scalar() or Decimal(0)
    except Exception:
        return Decimal(0)


def _cap_pct(pnl_usd: float, capital: Decimal) -> str:
    if not capital or capital <= 0:
        return ""
    pct = pnl_usd / float(capital) * 100
    sign = "+" if pct >= 0 else ""
    return f" | {sign}{pct:.2f}% capital"


def _fp(value: Decimal) -> str:
    v = float(value)
    if abs(v) >= 100:
        return f"${v:,.2f}"
    if abs(v) >= 1:
        return f"${v:,.4f}"
    return f"${v:,.6f}"


def _fq(value: Decimal) -> str:
    v = float(value)
    if v == int(v):
        return str(int(v))
    s = f"{v:.8f}".rstrip("0").rstrip(".")
    return s


def _fpct(value: Decimal) -> str:
    return f"{float(value):.2f}"


def _pnl_usd_str(side: str, entry: Decimal, target: Decimal, qty: Decimal) -> str:
    gross = float((target - entry) * qty) if side == "LONG" else float((entry - target) * qty)
    fees = float((entry + target) * qty) * 0.001  # 0.1% entry + 0.1% exit
    pnl = gross - fees
    sign = "+" if pnl >= 0 else "-"
    return f"{sign}${abs(pnl):,.0f}"


def _pct_distance(side: str, entry: Decimal, target: Decimal) -> str:
    if entry <= 0:
        return ""
    if side == "LONG":
        pct = (target - entry) / entry * 100
    else:
        pct = (entry - target) / entry * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{float(pct):.1f}%"


def _fmt_duration(opened_at: datetime | None, closed_at: datetime | None) -> str:
    if not opened_at or not closed_at:
        return "?"
    a = opened_at.replace(tzinfo=None) if opened_at.tzinfo else opened_at
    b = closed_at.replace(tzinfo=None) if closed_at.tzinfo else closed_at
    diff = (b - a).total_seconds()
    if diff < 60:
        return f"{int(diff)}s"
    if diff < 3600:
        return f"{int(diff // 60)}m"
    h = int(diff // 3600)
    m = int((diff % 3600) // 60)
    if h >= 24:
        d = h // 24
        h = h % 24
        return f"{d}j {h}h"
    return f"{h}h {m}m"


async def _save_setting(key: str, value: bool):
    async with async_session() as session:
        setting = await session.get(Setting, key)
        if setting:
            setting.value = str(value).lower()
            setting.updated_at = datetime.now(timezone.utc)
        else:
            session.add(Setting(
                key=key,
                value=str(value).lower(),
                updated_at=datetime.now(timezone.utc),
            ))
        await session.commit()


async def _load_setting(key: str, default: bool = False) -> bool:
    try:
        async with async_session() as session:
            setting = await session.get(Setting, key)
            if setting and setting.value:
                return setting.value.lower() == "true"
    except Exception:
        log.error("telegram_load_setting_failed", key=key, exc_info=True)
    return default
