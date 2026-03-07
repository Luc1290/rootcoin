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
_http: httpx.AsyncClient | None = None
_summary_task: asyncio.Task | None = None
SUMMARY_INTERVAL = 4 * 3600  # 4 heures

SETTING_KEY = "telegram_enabled"
SETTING_POSITIONS = "notify_positions"
SETTING_ORDERS = "notify_orders"
SETTING_LEVELS = "notify_levels"
SETTING_PNL = "notify_pnl"
_CATEGORY_KEYS = {SETTING_POSITIONS, SETTING_ORDERS, SETTING_LEVELS, SETTING_PNL}


async def start():
    global _enabled, _notify_positions, _notify_orders, _notify_levels, _notify_pnl, _http, _summary_task
    _http = httpx.AsyncClient(timeout=10)
    _enabled = await _load_setting(SETTING_KEY, is_configured())
    _notify_positions = await _load_setting(SETTING_POSITIONS, True)
    _notify_orders = await _load_setting(SETTING_ORDERS, True)
    _notify_levels = await _load_setting(SETTING_LEVELS, True)
    _notify_pnl = await _load_setting(SETTING_PNL, True)
    _summary_task = asyncio.create_task(_periodic_summary_loop())
    log.info("telegram_notifier_started", configured=is_configured(), enabled=_enabled)


async def stop():
    global _http, _summary_task
    if _summary_task:
        _summary_task.cancel()
        _summary_task = None
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


def get_categories() -> dict:
    return {
        SETTING_POSITIONS: _notify_positions,
        SETTING_ORDERS: _notify_orders,
        SETTING_LEVELS: _notify_levels,
        SETTING_PNL: _notify_pnl,
    }


# ── State mutations ──────────────────────────────────────────


async def set_enabled(enabled: bool):
    global _enabled
    _enabled = enabled
    await _save_setting(SETTING_KEY, enabled)
    log.info("telegram_enabled_changed", enabled=enabled)


async def set_category_enabled(key: str, enabled: bool):
    global _notify_positions, _notify_orders, _notify_levels, _notify_pnl
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
    msg = (
        f"\U0001f7e2 <b>{symbol} {side}</b> ouvert\n"
        f"Qty: {_fq(qty)} {base} @ {_fp(price)}\n"
        f"Marche: {market_type.replace('_', ' ')}"
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
        header = f"\u26d4 <b>{symbol} {side}</b> SL touche"
    elif exit_reason == "TP":
        header = f"\U0001f3af <b>{symbol} {side}</b> TP atteint"
    elif exit_reason == "OCO":
        oco_icon = "\U0001f3af" if win else "\u26d4"
        oco_label = "TP" if win else "SL"
        header = f"{oco_icon} <b>{symbol} {side}</b> OCO {oco_label} touche"
    else:
        icon = "\u2705" if win else "\u274c"
        header = f"\U0001f534 <b>{symbol} {side}</b> ferme {icon}"
    sign = "+" if net_pnl > 0 else ""
    sign_g = "+" if realized_pnl > 0 else ""
    duration = _fmt_duration(opened_at, closed_at)
    msg = (
        f"{header}\n"
        f"Entry: {_fp(entry_price)} \u2192 Exit: {_fp(exit_price)}\n"
        f"PnL brut: {sign_g}{_fp(realized_pnl)}\n"
        f"PnL net: {sign}{_fp(net_pnl)} ({sign}{_fq(pnl_pct)}%)\n"
        f"Duree: {duration}"
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
        f"Nouveau moy: {_fp(new_avg)} (total: {_fq(new_total)} {base})"
    )
    await notify(msg)


async def notify_position_opened_batch(
    symbol: str, side: str, avg_price: Decimal, total_qty: Decimal,
    market_type: str, num_fills: int,
):
    if not is_positions_enabled():
        return
    base = symbol.replace("USDC", "").replace("USDT", "")
    msg = (
        f"\U0001f7e2 <b>{symbol} {side}</b> ouvert\n"
        f"Qty: {_fq(total_qty)} {base} @ {_fp(avg_price)}\n"
        f"Marche: {market_type.replace('_', ' ')}\n"
        f"({num_fills} fills)"
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
        f"Nouveau moy: {_fp(avg_price)} (total: {_fq(new_total)} {base})"
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
        f"\U0001f4c9 <b>{symbol} {side}</b> reduit\n"
        f"-{_fq(qty)} {base} @ {_fp(price)}\n"
        f"PnL: {sign}{_fp(realized)}\n"
        f"Restant: {_fq(remaining)} {base}"
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
    lines = [f"\U0001f534 <b>{symbol} {side}</b> ferme (reconciliation)"]
    if exit_price and exit_price > 0:
        lines.append(f"Entry: {_fp(entry_price)} \u2192 Exit: {_fp(exit_price)}")
    else:
        lines.append(f"Entry: {_fp(entry_price)}")
    if net_pnl is not None:
        sign = "+" if net_pnl > 0 else ""
        icon = "\u2705" if net_pnl > 0 else "\u274c"
        lines.append(f"PnL net: {sign}{_fp(net_pnl)} {icon}")
    if pnl_pct is not None:
        sign = "+" if pnl_pct > 0 else ""
        lines.append(f"({sign}{_fq(pnl_pct)}%)")
    await notify("\n".join(lines), retries=2)


# ── Order notifications ──────────────────────────────────────


async def notify_sl_placed(
    symbol: str, side: str, stop_price: Decimal, qty: Decimal, entry_price: Decimal,
):
    if not is_orders_enabled():
        return
    dist_pct = _pct_distance(side, entry_price, stop_price)
    base = symbol.replace("USDC", "").replace("USDT", "")
    msg = (
        f"\u26d4 <b>{symbol}</b> SL place\n"
        f"Stop: {_fp(stop_price)} ({dist_pct})\n"
        f"Qty: {_fq(qty)} {base}"
    )
    await notify(msg)


async def notify_tp_placed(
    symbol: str, side: str, tp_price: Decimal, qty: Decimal, entry_price: Decimal,
):
    if not is_orders_enabled():
        return
    dist_pct = _pct_distance(side, entry_price, tp_price)
    base = symbol.replace("USDC", "").replace("USDT", "")
    msg = (
        f"\U0001f3af <b>{symbol}</b> TP place\n"
        f"Target: {_fp(tp_price)} ({dist_pct})\n"
        f"Qty: {_fq(qty)} {base}"
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
    base = symbol.replace("USDC", "").replace("USDT", "")
    msg = (
        f"\U0001f500 <b>{symbol}</b> OCO place\n"
        f"TP: {_fp(tp_price)} ({tp_dist}) | SL: {_fp(sl_price)} ({sl_dist})\n"
        f"Qty: {_fq(qty)} {base}"
    )
    await notify(msg)


async def notify_position_secured(
    symbol: str, side: str, half_qty: Decimal, sl_price: Decimal, remaining: Decimal,
):
    if not is_orders_enabled():
        return
    base = symbol.replace("USDC", "").replace("USDT", "")
    msg = (
        f"\U0001f6e1\ufe0f <b>{symbol} {side}</b> securise\n"
        f"Vendu: {_fq(half_qty)} {base} au marche\n"
        f"Restant: {_fq(remaining)} {base} avec SL breakeven @ {_fp(sl_price)}"
    )
    await notify(msg)


async def notify_trailing_moved(
    symbol: str, side: str, sl_price: Decimal, tp_price: Decimal,
    entry_price: Decimal, gain_pct: Decimal, is_breakeven: bool,
):
    if not is_orders_enabled():
        return
    sl_dist = _pct_distance(side, entry_price, sl_price)
    tp_dist = _pct_distance(side, entry_price, tp_price)
    if is_breakeven:
        header = f"\U0001f6e1\ufe0f <b>{symbol} {side}</b> SL au breakeven"
    else:
        header = f"\u2b06\ufe0f <b>{symbol} {side}</b> trailing SL remonte"
    msg = (
        f"{header}\n"
        f"Gain: +{_fq(gain_pct)}%\n"
        f"SL: {_fp(sl_price)} ({sl_dist}) | TP: {_fp(tp_price)} ({tp_dist})"
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
        header = f"\u2696\ufe0f <b>{symbol} {side}</b> retour au breakeven"
    else:
        icon = "\U0001f4c8" if threshold > 0 else "\U0001f4c9"
        t_sign = "+" if threshold > 0 else ""
        header = f"{icon} <b>{symbol} {side}</b> atteint {t_sign}{threshold}%"
    msg = (
        f"{header}\n"
        f"PnL: {sign}{_fp(pnl_usd)} ({sign}{_fq(pnl_pct)}%)\n"
        f"Entry: {_fp(entry_price)} \u2192 Prix: {_fp(current_price)}"
    )
    await notify(msg)


# ── Startup notification ──────────────────────────────────────


async def notify_startup_summary():
    if not is_enabled():
        return
    from backend.trading import position_tracker, pnl
    positions = position_tracker.get_positions()
    if not positions:
        await notify("\U0001f680 <b>RootCoin demarre</b>\nAucune position active.")
        return
    lines = [f"\U0001f680 <b>RootCoin demarre</b> \u2014 {len(positions)} position(s) active(s)\n"]
    for pos in positions:
        unrealized = Decimal("0")
        if pos.current_price and pos.current_price > 0:
            unrealized, _ = pnl.unrealized_pnl(
                pos.side, pos.entry_price, pos.current_price,
                pos.quantity, pos.entry_fees_usd,
            )
        sign = "+" if unrealized > 0 else ""
        base = pos.symbol.replace("USDC", "").replace("USDT", "")
        lines.append(
            f"\u2022 <b>{pos.symbol}</b> {pos.side} \u2014 "
            f"{_fq(pos.quantity)} {base} @ {_fp(pos.entry_price)} "
            f"({sign}{_fp(unrealized)})"
        )
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
    lines = [f"\U0001f4ca <b>Resume positions</b> \u2014 {len(positions)} active(s)\n"]
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
        base = pos.symbol.replace("USDC", "").replace("USDT", "")
        lines.append(
            f"\u2022 <b>{pos.symbol}</b> {pos.side} \u2014 "
            f"{_fq(pos.quantity)} {base} @ {_fp(pos.entry_price)} "
            f"\u2192 {_fp(pos.current_price or Decimal('0'))} "
            f"({sign}{_fq(u_pct)}%)"
        )
    t_sign = "+" if total_pnl > 0 else ""
    lines.append(f"\n<b>Total unrealized: {t_sign}{_fp(total_pnl)}</b>")
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
        f"{arrow} <b>{symbol}</b> alerte prix atteinte",
        f"Prix: {_fp(price)} ({label} de {_fp(target_price)})",
    ]
    if note:
        lines.append(f"Note: {note}")
    await notify("\n".join(lines))


async def notify_level_reached(
    symbol: str, price: Decimal, level_price: str, level_type: str, label: str,
):
    if not is_levels_enabled():
        return
    lp = Decimal(level_price)
    msg = (
        f"\U0001f4cd <b>{symbol}</b> touche {label}\n"
        f"Prix: {_fp(price)} (level: {_fp(lp)})"
    )
    await notify(msg)


# ── Helpers ──────────────────────────────────────────────────


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
