"""Smart OCO Trailing — auto-place OCO on new positions using key levels,
then trail SL+TP up as profit grows.

Settings (DB key -> default):
  trailing_enabled    -> "1"
  trailing_activation -> "0.5"   (gain % for first move -> breakeven)
  trailing_breakeven  -> "0.2"   (SL % for first move, covers fees)
  trailing_step       -> "0.15"  (min gain increment between level checks)
  trailing_offset     -> "0.5"   (SL = gain - offset%)
  trailing_tp_guard   -> "0.3"   (% proximity to TP -> force move, works pre-activation)
  trailing_min_rr     -> "1.5"   (min R:R for initial OCO)
"""

import asyncio
import time as _time
from decimal import Decimal

import structlog
from sqlalchemy import select

from backend.core.database import async_session
from backend.core.models import Setting
from backend.exchange.symbol_filters import round_price
from backend.exchange.ws_manager import EVENT_PRICE_UPDATE
from backend.market import market_analyzer
from backend.routes.position_helpers import fetch_order_prices
from backend.services import telegram_notifier
from backend.trading import order_manager, position_tracker

log = structlog.get_logger()

# Defaults
_DEF_ACTIVATION = Decimal("0.5")
_DEF_BREAKEVEN = Decimal("0.2")
_DEF_STEP = Decimal("0.15")
_DEF_OFFSET = Decimal("0.5")
_DEF_TP_GUARD = Decimal("0.3")
_DEF_MIN_RR = Decimal("1.5")
_MAX_SL_GAP = Decimal("0.9")  # max unprotected gain % before forcing SL advance

# Timings
_POLL_INTERVAL = 3.0
_FILL_WAIT = 6.0        # wait for multi-fill to complete
_NAKED_GRACE = 90.0     # seconds before re-placing OCO on unprotected position
_MIN_MOVE_INTERVAL = 60.0  # minimum seconds between consecutive trailing moves
_TP_GUARD_INTERVAL = 15.0  # shorter interval for TP guard (urgent)
_RESNAP_INTERVAL = 300.0   # re-evaluate SL from key levels every 5 minutes
_OVERRIDE_REMINDER = 7200.0  # remind user after 2h of manual control

# State
_tracked: dict[int, dict] = {}
_known_ids: set[int] = set()
_naked_since: dict[int, float] = {}  # pos_id -> monotonic timestamp
_settings: dict = {}
_running = False
_monitor_task: asyncio.Task | None = None


# ── Settings ─────────────────────────────────────────────────

async def _load_settings():
    global _settings
    async with async_session() as session:
        rows = (await session.execute(
            select(Setting).where(Setting.key.like("trailing_%"))
        )).scalars().all()
    s = {r.key: r.value for r in rows}
    _settings = {
        "enabled": s.get("trailing_enabled", "1") == "1",
        "activation": Decimal(s.get("trailing_activation", str(_DEF_ACTIVATION))),
        "breakeven": Decimal(s.get("trailing_breakeven", str(_DEF_BREAKEVEN))),
        "step": Decimal(s.get("trailing_step", str(_DEF_STEP))),
        "offset": Decimal(s.get("trailing_offset", str(_DEF_OFFSET))),
        "tp_guard": Decimal(s.get("trailing_tp_guard", str(_DEF_TP_GUARD))),
        "min_rr": Decimal(s.get("trailing_min_rr", str(_DEF_MIN_RR))),
    }


# ── Lifecycle ────────────────────────────────────────────────

async def start():
    global _running, _monitor_task
    await _load_settings()
    if not _settings.get("enabled", True):
        log.info("trailing_manager_disabled")
        return

    positions = position_tracker.get_positions()
    active = [p for p in positions if p.is_active]
    _known_ids.update(p.id for p in active)

    # Resume tracking for positions that already have OCO orders
    await _resume_existing(active)

    from backend.exchange import ws_manager
    ws_manager.on(EVENT_PRICE_UPDATE, _handle_price_update)

    _running = True
    _monitor_task = asyncio.create_task(_position_monitor_loop())
    log.info("trailing_manager_started", known=len(_known_ids), resumed=len(_tracked))


async def _resume_existing(positions):
    """Rebuild tracking state for positions that have orders after restart."""
    # Resume positions with OCO or individual SL+TP (margin OCO may lose oco ref)
    candidates = [p for p in positions
                  if p.oco_order_list_id or (p.sl_order_id and p.tp_order_id)]
    if not candidates:
        return

    order_prices = await fetch_order_prices([p.id for p in candidates])

    for pos in candidates:
        prices = order_prices.get(pos.id, {})
        sl_str = prices.get("sl_price")
        tp_str = prices.get("tp_price")
        if not sl_str or not tp_str:
            continue

        sl_decimal = Decimal(sl_str)
        if pos.side == "LONG":
            resumed_pct = (sl_decimal - pos.entry_price) / pos.entry_price * 100
        else:
            resumed_pct = (pos.entry_price - sl_decimal) / pos.entry_price * 100
        is_trailing = resumed_pct > _DEF_BREAKEVEN

        _tracked[pos.id] = {
            "auto_sl": sl_decimal,
            "auto_tp": Decimal(tp_str),
            "oco_list_id": pos.oco_order_list_id,
            "trailing_active": is_trailing,
            "manual_override": False,
            "moving": False,
            "last_move_at": 0.0,
            "last_step_pct": resumed_pct + _DEF_OFFSET if is_trailing else Decimal(0),
        }
        log.info("trailing_resumed", symbol=pos.symbol, pos_id=pos.id,
                 sl=sl_str, tp=tp_str, has_oco=bool(pos.oco_order_list_id))


async def stop():
    global _running
    _running = False
    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
    _tracked.clear()
    _known_ids.clear()
    _naked_since.clear()
    log.info("trailing_manager_stopped")


# ── Position monitor ─────────────────────────────────────────

async def _position_monitor_loop():
    while _running:
        try:
            await asyncio.sleep(_POLL_INTERVAL)
            positions = position_tracker.get_positions()
            active = {p.id: p for p in positions if p.is_active}

            # New positions
            for pid, pos in active.items():
                if pid not in _known_ids:
                    asyncio.create_task(_handle_new_position(pid))

            # Closed positions → cleanup
            closed = _known_ids - set(active.keys())
            for pid in closed:
                _tracked.pop(pid, None)
                _naked_since.pop(pid, None)

            _known_ids.clear()
            _known_ids.update(active.keys())

            # Detect manual overrides (skip positions mid-move)
            for pid, tracking in list(_tracked.items()):
                pos = active.get(pid)
                if pos and not tracking.get("manual_override") and not tracking.get("moving"):
                    _check_manual_override(pos, tracking)

            # Remind user if manual control has been active for too long
            for pid, tracking in list(_tracked.items()):
                if not tracking.get("manual_override"):
                    continue
                if tracking.get("override_reminded"):
                    continue
                override_at = tracking.get("override_at", 0.0)
                if override_at and _time.monotonic() - override_at >= _OVERRIDE_REMINDER:
                    pos = active.get(pid)
                    if pos:
                        tracking["override_reminded"] = True
                        asyncio.create_task(_send_override_reminder(pos, tracking))

            # Detect naked positions (no SL/TP/OCO) and recover after grace period
            for pid, pos in active.items():
                has_orders = pos.sl_order_id or pos.tp_order_id or pos.oco_order_list_id
                if has_orders:
                    _naked_since.pop(pid, None)
                elif pid not in _naked_since:
                    _naked_since[pid] = _time.monotonic()
                    log.warning("trailing_position_naked", symbol=pos.symbol, pos_id=pid)
                elif _time.monotonic() - _naked_since[pid] > _NAKED_GRACE:
                    _naked_since.pop(pid)
                    asyncio.create_task(_recover_naked_position(pid))

        except asyncio.CancelledError:
            break
        except Exception:
            log.error("trailing_monitor_error", exc_info=True)


async def _handle_new_position(pos_id: int):
    await asyncio.sleep(_FILL_WAIT)
    if not _running:
        return

    pos = _get_pos(pos_id)
    if not pos:
        return

    # Skip if user already placed orders
    if pos.sl_order_id or pos.tp_order_id or pos.oco_order_list_id:
        log.info("trailing_skip_has_orders", symbol=pos.symbol, pos_id=pos_id)
        return

    analysis = market_analyzer.get_analysis(pos.symbol)
    if not analysis:
        log.info("trailing_skip_no_analysis", symbol=pos.symbol)
        return

    key_levels = analysis.get("key_levels", [])
    if not key_levels:
        log.info("trailing_skip_no_levels", symbol=pos.symbol)
        return

    sl_price, tp_price = _find_initial_sl_tp(key_levels, pos.entry_price, pos.side)
    if not sl_price or not tp_price:
        log.info("trailing_skip_no_sl_tp", symbol=pos.symbol)
        return

    # Check and adjust R:R
    min_rr = _settings.get("min_rr", _DEF_MIN_RR)
    rr = _compute_rr(pos.entry_price, sl_price, tp_price, pos.side)
    if rr < min_rr:
        sl_price, tp_price = _adjust_for_rr(
            key_levels, pos.entry_price, pos.side, min_rr,
        )
        if not sl_price or not tp_price:
            log.info("trailing_skip_low_rr", symbol=pos.symbol, rr=str(rr))
            return
        rr = _compute_rr(pos.entry_price, sl_price, tp_price, pos.side)

    sl_price = round_price(pos.symbol, sl_price)
    tp_price = round_price(pos.symbol, tp_price)

    try:
        result = await order_manager.place_oco(pos, tp_price, sl_price, silent=True)
        oco_id = str(result.get("orderListId", ""))
        _tracked[pos.id] = {
            "auto_sl": sl_price,
            "auto_tp": tp_price,
            "oco_list_id": oco_id,
            "trailing_active": False,
            "manual_override": False,
            "moving": False,
            "last_move_at": 0.0,
            "last_step_pct": Decimal(0),
        }
        log.info("trailing_initial_oco", symbol=pos.symbol,
                 sl=str(sl_price), tp=str(tp_price), rr=f"{rr:.1f}")
    except Exception:
        log.error("trailing_initial_oco_failed", symbol=pos.symbol, exc_info=True)


async def _recover_naked_position(pos_id: int):
    """Re-place OCO after grace period on unprotected position."""
    pos = _get_pos(pos_id)
    if not pos:
        return
    if pos.sl_order_id or pos.tp_order_id or pos.oco_order_list_id:
        return  # orders placed during grace period

    current = pos.current_price
    if not current or current <= 0:
        return

    entry = pos.entry_price
    if pos.side == "LONG":
        gain_pct = (current - entry) / entry * 100
    else:
        gain_pct = (entry - current) / entry * 100

    analysis = market_analyzer.get_analysis(pos.symbol)
    if not analysis:
        log.warning("trailing_recovery_no_analysis", symbol=pos.symbol)
        return

    key_levels = analysis.get("key_levels", [])
    if not key_levels:
        return

    activation = _settings.get("activation", _DEF_ACTIVATION)

    if gain_pct >= activation:
        # Already in profit: compute trailing SL from current gain
        offset = _settings.get("offset", _DEF_OFFSET)
        sl_pct = max(gain_pct - offset, _settings.get("breakeven", _DEF_BREAKEVEN))
        if pos.side == "LONG":
            sl_price = round_price(pos.symbol, entry * (1 + sl_pct / 100))
        else:
            sl_price = round_price(pos.symbol, entry * (1 - sl_pct / 100))

        # Find next TP beyond current price
        tp_price = _find_next_resistance(key_levels, current, pos.side)
        if not tp_price:
            # Fallback: TP at current gain + step
            step = _settings.get("step", _DEF_STEP)
            if pos.side == "LONG":
                tp_price = round_price(pos.symbol, entry * (1 + (gain_pct + step) / 100))
            else:
                tp_price = round_price(pos.symbol, entry * (1 - (gain_pct + step) / 100))

        trailing_active = True
        last_step_pct = gain_pct
        log.info("trailing_recovery_in_profit", symbol=pos.symbol,
                 gain=f"{gain_pct:.2f}%", sl_pct=f"{sl_pct:.2f}%")
    else:
        # Below activation: place initial OCO from key levels
        sl_price, tp_price = _find_initial_sl_tp(key_levels, entry, pos.side)
        if not sl_price or not tp_price:
            return

        min_rr = _settings.get("min_rr", _DEF_MIN_RR)
        rr = _compute_rr(entry, sl_price, tp_price, pos.side)
        if rr < min_rr:
            sl_price, tp_price = _adjust_for_rr(
                key_levels, entry, pos.side, min_rr,
            )
            if not sl_price or not tp_price:
                return

        trailing_active = False
        last_step_pct = Decimal(0)

    sl_price = round_price(pos.symbol, sl_price)
    tp_price = round_price(pos.symbol, tp_price)

    try:
        result = await order_manager.place_oco(pos, tp_price, sl_price, silent=True)
        oco_id = str(result.get("orderListId", ""))
        _tracked[pos.id] = {
            "auto_sl": sl_price,
            "auto_tp": tp_price,
            "oco_list_id": oco_id,
            "trailing_active": trailing_active,
            "manual_override": False,
            "moving": False,
            "last_move_at": _time.monotonic(),
            "last_step_pct": last_step_pct,
        }
        log.info("trailing_naked_recovery", symbol=pos.symbol,
                 sl=str(sl_price), tp=str(tp_price),
                 trailing=trailing_active, gain=f"{gain_pct:.2f}%")
    except Exception:
        log.error("trailing_naked_recovery_failed", symbol=pos.symbol, exc_info=True)


def _sl_protection_pct(side: str, entry: Decimal, auto_sl: Decimal) -> Decimal:
    """Return the SL protection as a gain % relative to entry."""
    if not auto_sl or entry <= 0:
        return Decimal(0)
    if side == "LONG":
        return (auto_sl - entry) / entry * 100
    else:
        return (entry - auto_sl) / entry * 100


# ── Price handler (trailing logic) ───────────────────────────

async def _handle_price_update(msg: dict):
    if not _running or not _tracked:
        return

    symbol = msg.get("s", "")
    try:
        current_price = Decimal(msg.get("c", "0"))
    except Exception:
        return
    if current_price <= 0:
        return

    for pos_id, tracking in list(_tracked.items()):
        if tracking.get("moving"):
            continue

        pos = _get_pos(pos_id)
        if not pos or pos.symbol != symbol:
            continue

        entry = pos.entry_price
        if pos.side == "LONG":
            gain_pct = (current_price - entry) / entry * 100
        else:
            gain_pct = (entry - current_price) / entry * 100

        activation = _settings.get("activation", _DEF_ACTIVATION)
        tp_guard_triggered = False

        # ── TP Guard: check FIRST regardless of activation ──
        auto_tp = tracking["auto_tp"]
        if auto_tp:
            tp_guard = _settings.get("tp_guard", _DEF_TP_GUARD)
            if pos.side == "LONG":
                tp_dist_pct = (auto_tp - current_price) / current_price * 100
            else:
                tp_dist_pct = (current_price - auto_tp) / current_price * 100
            if Decimal(0) < tp_dist_pct <= tp_guard:
                tp_guard_triggered = True

        # Skip if below activation AND no TP guard
        if gain_pct < activation and not tp_guard_triggered:
            continue

        step = _settings.get("step", _DEF_STEP)
        last_step = tracking.get("last_step_pct", Decimal(0))
        should_move = False

        # ── Regular trailing logic ──
        if gain_pct >= activation:
            if not tracking.get("trailing_active"):
                should_move = True
            elif gain_pct >= last_step + step:
                # Only move if key levels offer a better SL or TP
                if _has_better_levels(pos.symbol, current_price, pos.side,
                                      tracking["auto_sl"], tracking["auto_tp"]):
                    should_move = True
                else:
                    # Safety net: force move if SL protection gap too large
                    sl_protection = _sl_protection_pct(
                        pos.side, entry, tracking["auto_sl"],
                    )
                    gap = gain_pct - sl_protection
                    if gap >= _MAX_SL_GAP:
                        should_move = True
                        log.info("trailing_safety_net",
                                 symbol=pos.symbol,
                                 gain=f"{gain_pct:.2f}%",
                                 sl_prot=f"{sl_protection:.2f}%",
                                 gap=f"{gap:.2f}%")
                    else:
                        # No better levels and gap OK: defer next check
                        tracking["last_step_pct"] = gain_pct

        # ── TP Guard override: force move if approaching TP ──
        if tp_guard_triggered and not should_move:
            should_move = True
            log.info("trailing_tp_guard", symbol=pos.symbol,
                     gain=f"{gain_pct:.2f}%", tp_dist=f"{tp_dist_pct:.3f}%")

        # Periodic re-snap: re-evaluate SL from current key levels
        if not should_move and tracking.get("trailing_active"):
            now = _time.monotonic()
            last_resnap = tracking.get("last_resnap_at", 0.0)
            if now - last_resnap >= _RESNAP_INTERVAL:
                tracking["last_resnap_at"] = now
                offset = _settings.get("offset", _DEF_OFFSET)
                analysis = market_analyzer.get_analysis(pos.symbol)
                if analysis:
                    candidate = _find_trailing_sl_level(
                        analysis.get("key_levels", []), current_price, pos.side,
                        min_dist_pct=offset,
                    )
                    current_auto_sl = tracking["auto_sl"]
                    if candidate:
                        is_better = (
                            (pos.side == "SHORT" and candidate < current_auto_sl) or
                            (pos.side == "LONG" and candidate > current_auto_sl)
                        )
                        if is_better:
                            should_move = True
                            log.info("trailing_resnap_triggered", symbol=symbol,
                                     old_sl=str(current_auto_sl), new_candidate=str(candidate))

        if not should_move:
            continue

        # Manual override: trailing takes back control
        if tracking.get("manual_override"):
            tracking["manual_override"] = False
            tracking.pop("override_at", None)
            tracking.pop("override_reminded", None)
            log.info("trailing_resuming", symbol=pos.symbol, gain=f"{gain_pct:.2f}%")

        # Rate-limit: shorter for TP guard (urgent)
        last_move = tracking.get("last_move_at", 0.0)
        rate_limit = _TP_GUARD_INTERVAL if tp_guard_triggered else _MIN_MOVE_INTERVAL
        if last_move and _time.monotonic() - last_move < rate_limit:
            continue

        is_breakeven = not tracking.get("trailing_active")
        asyncio.create_task(_move_oco(
            pos, tracking, gain_pct, current_price, is_breakeven,
            tp_guard=tp_guard_triggered,
        ))


async def _move_oco(pos, tracking, gain_pct, current_price, is_breakeven=False, tp_guard=False):
    tracking["moving"] = True
    try:
        analysis = market_analyzer.get_analysis(pos.symbol)
        key_levels = analysis.get("key_levels", []) if analysis else []
        entry = pos.entry_price
        offset = _settings.get("offset", _DEF_OFFSET)

        # --- Compute SL ---
        if is_breakeven:
            # Breakeven: percentage-based near entry (covers fees)
            breakeven = _settings.get("breakeven", _DEF_BREAKEVEN)
            if pos.side == "LONG":
                new_sl_price = entry * (1 + breakeven / 100)
            else:
                new_sl_price = entry * (1 - breakeven / 100)
        else:
            # Trailing: snap to nearest key level with min offset% distance
            new_sl_price = _find_trailing_sl_level(
                key_levels, current_price, pos.side, min_dist_pct=offset,
            )
            if not new_sl_price:
                # Fallback: percentage-based
                sl_pct = gain_pct - offset
                if pos.side == "LONG":
                    new_sl_price = entry * (1 + sl_pct / 100)
                else:
                    new_sl_price = entry * (1 - sl_pct / 100)

        # SL never goes backwards (TP guard: keep current SL, still move TP)
        current_auto_sl = tracking["auto_sl"]
        if pos.side == "LONG" and new_sl_price <= current_auto_sl:
            if not tp_guard:
                return
            new_sl_price = current_auto_sl
        if pos.side == "SHORT" and new_sl_price >= current_auto_sl:
            if not tp_guard:
                return
            new_sl_price = current_auto_sl

        # --- Compute TP from key levels ---
        new_tp = tracking["auto_tp"]
        candidate = _find_next_resistance(key_levels, current_price, pos.side)
        if candidate:
            new_tp = candidate

        # Re-fetch position for fresh state
        pos = _get_pos(pos.id)
        if not pos:
            return

        new_sl_price = round_price(pos.symbol, new_sl_price)
        new_tp = round_price(pos.symbol, new_tp)

        # Nothing changed: skip useless OCO replace
        if new_sl_price == tracking["auto_sl"] and new_tp == tracking["auto_tp"]:
            return

        result = await order_manager.place_oco(pos, new_tp, new_sl_price, silent=True)
        oco_id = str(result.get("orderListId", ""))

        tracking.update({
            "auto_sl": new_sl_price,
            "auto_tp": new_tp,
            "oco_list_id": oco_id,
            "trailing_active": True,
            "manual_override": False,
            "last_move_at": _time.monotonic(),
            "last_step_pct": gain_pct,
        })

        sl_source = "level" if not is_breakeven else "breakeven"
        log.info("trailing_oco_moved", symbol=pos.symbol,
                 sl=str(new_sl_price), tp=str(new_tp), source=sl_source)

        asyncio.create_task(telegram_notifier.notify_trailing_moved(
            pos.symbol, pos.side, new_sl_price, new_tp,
            pos.entry_price, current_price, pos.quantity,
            gain_pct, is_breakeven=is_breakeven,
        ))
    except Exception:
        log.error("trailing_move_failed", symbol=pos.symbol, exc_info=True)
        # place_oco cancelled old orders then failed to place new ones
        # => position is naked. Retry OCO with fresh prices, else fallback SL.
        await _retry_oco_or_fallback_sl(pos, tracking)
    finally:
        tracking["moving"] = False


async def _retry_oco_or_fallback_sl(pos, tracking):
    try:
        pos = _get_pos(pos.id)
        if not pos or pos.oco_order_list_id or pos.sl_order_id:
            return

        entry = pos.entry_price
        current = pos.current_price
        if not current or current <= 0:
            raise ValueError("no current price")

        # Recalculate SL from current gain
        offset = _settings.get("offset", _DEF_OFFSET)
        if pos.side == "LONG":
            gain_pct = (current - entry) / entry * 100
            sl = round_price(pos.symbol, entry * (1 + (gain_pct - offset) / 100))
        else:
            gain_pct = (entry - current) / entry * 100
            sl = round_price(pos.symbol, entry * (1 - (gain_pct - offset) / 100))

        # Find fresh TP
        tp = tracking["auto_tp"]
        analysis = market_analyzer.get_analysis(pos.symbol)
        if analysis:
            candidate = _find_next_resistance(
                analysis.get("key_levels", []), current, pos.side,
            )
            if candidate:
                tp = round_price(pos.symbol, candidate)

        result = await order_manager.place_oco(pos, tp, sl, silent=True)
        oco_id = str(result.get("orderListId", ""))
        tracking.update({
            "auto_sl": sl, "auto_tp": tp, "oco_list_id": oco_id,
            "last_move_at": _time.monotonic(),
        })
        log.info("trailing_retry_oco_ok", symbol=pos.symbol, sl=str(sl), tp=str(tp))
    except Exception:
        log.error("trailing_retry_oco_failed", symbol=pos.symbol, exc_info=True)
        # Last resort: simple SL
        try:
            pos = _get_pos(pos.id)
            if not pos or pos.oco_order_list_id or pos.sl_order_id:
                return
            sl = tracking["auto_sl"]
            await order_manager.place_stop_loss(pos, sl)
            tracking["oco_list_id"] = None
            tracking["manual_override"] = True
            log.warning("trailing_fallback_sl", symbol=pos.symbol, sl=str(sl))
        except Exception:
            log.error("trailing_fallback_sl_failed", symbol=pos.symbol, exc_info=True)


# ── Level helpers ────────────────────────────────────────────

def _parse_levels(key_levels: list) -> list[Decimal]:
    result = []
    for l in key_levels:
        try:
            result.append(Decimal(str(l["price"])))
        except (KeyError, ValueError, TypeError):
            continue
    result.sort()
    return result


def _has_better_levels(symbol, current_price, side, current_sl, current_tp):
    """Check if key levels offer a better SL or TP than current (in-memory, fast)."""
    analysis = market_analyzer.get_analysis(symbol)
    if not analysis:
        return False
    key_levels = analysis.get("key_levels", [])
    offset = _settings.get("offset", _DEF_OFFSET)
    sl = _find_trailing_sl_level(key_levels, current_price, side, min_dist_pct=offset)
    if sl:
        if (side == "LONG" and sl > current_sl) or \
           (side == "SHORT" and sl < current_sl):
            return True
    tp = _find_next_resistance(key_levels, current_price, side)
    if tp:
        if (side == "LONG" and tp > current_tp) or \
           (side == "SHORT" and tp < current_tp):
            return True
    return False


def _find_initial_sl_tp(key_levels, entry_price, side):
    prices = _parse_levels(key_levels)
    if not prices:
        return None, None

    min_dist = Decimal("0.5")  # skip levels < 0.5% away (covers fees + noise)

    if side == "LONG":
        sl = None
        for p in reversed(prices):
            if p < entry_price:
                dist = (entry_price - p) / entry_price * 100
                if dist >= min_dist:
                    sl = p
                    break
        tp = None
        for p in prices:
            if p > entry_price:
                dist = (p - entry_price) / entry_price * 100
                if dist >= min_dist:
                    tp = p
                    break
    else:
        sl = None
        for p in prices:
            if p > entry_price:
                dist = (p - entry_price) / entry_price * 100
                if dist >= min_dist:
                    sl = p
                    break
        tp = None
        for p in reversed(prices):
            if p < entry_price:
                dist = (entry_price - p) / entry_price * 100
                if dist >= min_dist:
                    tp = p
                    break

    return sl, tp


def _adjust_for_rr(key_levels, entry_price, side, min_rr):
    prices = _parse_levels(key_levels)
    min_dist = Decimal("0.5")

    if side == "LONG":
        below = [p for p in prices
                 if p < entry_price and (entry_price - p) / entry_price * 100 >= min_dist]
        above = [p for p in prices
                 if p > entry_price and (p - entry_price) / entry_price * 100 >= min_dist]
        for sl in reversed(below):
            risk = entry_price - sl
            for tp in above:
                reward = tp - entry_price
                if risk > 0 and reward / risk >= min_rr:
                    return sl, tp
    else:
        above = [p for p in prices
                 if p > entry_price and (p - entry_price) / entry_price * 100 >= min_dist]
        below = [p for p in prices
                 if p < entry_price and (entry_price - p) / entry_price * 100 >= min_dist]
        for sl in above:
            risk = sl - entry_price
            for tp in reversed(below):
                reward = entry_price - tp
                if risk > 0 and reward / risk >= min_rr:
                    return sl, tp

    return None, None


def _find_trailing_sl_level(key_levels, current_price, side, min_dist_pct=Decimal("0.8")):
    """Find nearest key level for trailing SL.

    SHORT: resistance ABOVE current_price (stop buy triggers there on bounce).
    LONG: support BELOW current_price (stop sell triggers there on pullback).
    """
    prices = _parse_levels(key_levels)
    if not prices:
        return None

    if side == "SHORT":
        for p in prices:
            if p <= current_price:
                continue
            dist = (p - current_price) / current_price * 100
            if dist >= min_dist_pct:
                return p
    else:
        for p in reversed(prices):
            if p >= current_price:
                continue
            dist = (current_price - p) / current_price * 100
            if dist >= min_dist_pct:
                return p
    return None


def _find_next_resistance(key_levels, current_price, side):
    prices = _parse_levels(key_levels)
    min_dist = Decimal("0.5")

    if side == "LONG":
        for p in prices:
            if p > current_price:
                dist = (p - current_price) / current_price * 100
                if dist >= min_dist:
                    return p
    else:
        for p in reversed(prices):
            if p < current_price:
                dist = (current_price - p) / current_price * 100
                if dist >= min_dist:
                    return p
    return None


def _compute_rr(entry, sl, tp, side):
    if side == "LONG":
        risk = entry - sl
        reward = tp - entry
    else:
        risk = sl - entry
        reward = entry - tp
    if risk <= 0:
        return Decimal(0)
    return reward / risk


async def _send_override_reminder(pos, tracking):
    """Send Telegram reminder that trailing is paused on this position."""
    try:
        sl_str = ""
        prices = await fetch_order_prices([pos.id])
        p = prices.get(pos.id, {})
        if p.get("sl_price"):
            sl_str = f"\nSL actuel : {p['sl_price']}"
        if p.get("tp_price"):
            sl_str += f" | TP : {p['tp_price']}"

        hours = int((_time.monotonic() - tracking.get("override_at", 0)) / 3600)
        duration = f"{hours}h" if hours >= 1 else "un moment"

        entry = pos.entry_price
        current = pos.current_price or entry
        if pos.side == "LONG":
            gain_pct = (current - entry) / entry * 100
        else:
            gain_pct = (entry - current) / entry * 100
        sign = "+" if gain_pct >= 0 else ""

        msg = (
            f"\u23f0 <b>{pos.symbol} {pos.side}</b> — trailing en pause depuis {duration}\n"
            f"Tu geres manuellement cette position.{sl_str}\n"
            f"Gain : {sign}{gain_pct:.2f}%\n"
            f"Le trailing reprendra automatiquement quand le prix progressera."
        )
        await telegram_notifier.notify(msg)
        log.info("trailing_override_reminder_sent", symbol=pos.symbol, hours=hours)
    except Exception:
        log.error("trailing_override_reminder_failed", symbol=pos.symbol, exc_info=True)


def _check_manual_override(pos, tracking):
    # Skip check if trailing moved recently (OCO IDs may be out of sync)
    last_move = tracking.get("last_move_at", 0.0)
    if last_move and _time.monotonic() - last_move < 15.0:
        return

    our_oco = tracking.get("oco_list_id")
    current_oco = pos.oco_order_list_id

    # Normalize to string for comparison (API may return int or str)
    if our_oco and current_oco:
        if str(current_oco) == str(our_oco):
            return

    if current_oco and current_oco != our_oco:
        tracking["manual_override"] = True
        tracking.setdefault("override_at", _time.monotonic())
        tracking["override_reminded"] = False
        log.info("trailing_manual_override", symbol=pos.symbol, reason="different_oco")
    elif not current_oco and (pos.sl_order_id or pos.tp_order_id):
        # Margin OCO may not store oco_order_list_id — if we also have no oco_list_id, skip
        if our_oco:
            tracking["manual_override"] = True
            tracking.setdefault("override_at", _time.monotonic())
            tracking["override_reminded"] = False
            log.info("trailing_manual_override", symbol=pos.symbol, reason="individual_orders")
    elif not current_oco and not pos.sl_order_id and not pos.tp_order_id and our_oco:
        tracking["manual_override"] = True
        tracking.setdefault("override_at", _time.monotonic())
        tracking["override_reminded"] = False
        log.info("trailing_manual_override", symbol=pos.symbol, reason="orders_removed")


def _get_pos(pos_id: int):
    positions = position_tracker.get_positions()
    return next((p for p in positions if p.id == pos_id and p.is_active), None)


# ── Public API ───────────────────────────────────────────────

def get_tracked() -> dict:
    result = {}
    for pid, t in _tracked.items():
        result[pid] = {
            "auto_sl": str(t["auto_sl"]),
            "auto_tp": str(t["auto_tp"]),
            "trailing_active": t["trailing_active"],
            "manual_override": t["manual_override"],
        }
    return result


async def resume_after_secure(pos_id: int):
    """Re-place OCO immediately after secure (half sold, SL-only remaining)."""
    tracking = _tracked.get(pos_id)
    if not tracking:
        return
    tracking["manual_override"] = False
    tracking.pop("override_at", None)
    tracking.pop("override_reminded", None)
    tracking["last_move_at"] = 0.0

    await asyncio.sleep(2.0)  # wait for order state to settle

    pos = _get_pos(pos_id)
    if not pos:
        return

    current = pos.current_price
    if not current or current <= 0:
        return

    entry = pos.entry_price
    if pos.side == "LONG":
        gain_pct = (current - entry) / entry * 100
    else:
        gain_pct = (entry - current) / entry * 100

    if gain_pct >= _settings.get("breakeven", _DEF_BREAKEVEN):
        await _move_oco(pos, tracking, gain_pct, current, is_breakeven=False)
        log.info("trailing_resumed_after_secure", symbol=pos.symbol, gain=f"{gain_pct:.2f}%")


def is_naked(pos_id: int) -> bool:
    return pos_id in _naked_since
