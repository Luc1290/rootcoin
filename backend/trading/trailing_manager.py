"""Smart OCO Trailing — auto-place OCO on new positions using key levels,
then trail SL+TP up as profit grows.

Capital-aware: reads portfolio total from balance snapshots to:
  - Cap max loss per trade to 2.5% of capital
  - Lock 50% of gain once it exceeds 0.8% of capital (fills the breakeven dead zone)

Settings (DB key -> default):
  trailing_enabled    -> "1"
  trailing_activation -> "0.5"   (gain % for first move -> breakeven)
  trailing_breakeven  -> "0.2"   (SL % for first move, covers fees)
  trailing_step       -> "0.15"  (min gain increment between level checks)
  trailing_offset     -> "0.5"   (SL = gain - offset%)
  trailing_tp_guard   -> "0.3"   (% proximity to TP -> force move, works pre-activation)
  trailing_min_rr     -> "1.5"   (min R:R for initial OCO)
  trailing_tighten_after -> "1"  (hours before tightening stale/ranging OR trailing-stalled positions, 0=disabled)
"""

import asyncio
import time as _time
from datetime import datetime, timezone
from decimal import Decimal

import structlog
from sqlalchemy import func, select

from backend.core.database import async_session
from backend.core.models import Balance, Setting
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
_MAX_SL_GAP = Decimal("0.5")  # max unprotected gain % before forcing SL advance
_DEF_TIGHTEN_AFTER = Decimal("1")  # hours before tightening stale/ranging positions
_TIGHTEN_INTERVAL = 1800.0  # 30min between tighten re-evaluations
_TIGHTEN_MIN_RR = Decimal("1.0")  # relaxed R:R for tightening (vs 1.5 initial)
_TIGHTEN_GAP_REDUCTION = Decimal("0.3")  # reduce SL/TP gap by 30% when no key levels
_DEF_FALLBACK_SL_PCT = Decimal("1")  # fallback SL distance % when no key levels
_DEF_MAX_SL_PCT = Decimal("1")      # max SL distance % from entry (cap losses)

# Capital-aware trailing
_CAP_MAX_RISK_PCT = Decimal("2.5")   # max loss per trade as % of capital
_CAP_LOCK_THRESHOLD = Decimal("0.8") # lock gains above this % of capital
_CAP_LOCK_RATIO = Decimal("0.5")     # lock this fraction of gain above threshold

# Timings
_POLL_INTERVAL = 3.0
_FILL_WAIT = 6.0        # wait for multi-fill to complete
_NAKED_GRACE = 90.0     # seconds before re-placing OCO on unprotected position
_MIN_MOVE_INTERVAL = 60.0  # minimum seconds between consecutive trailing moves
_TP_GUARD_INTERVAL = 1.5   # near-zero cooldown: orders are free, only debounce rapid oscillations
_RESNAP_INTERVAL = 300.0   # re-evaluate SL from key levels every 5 minutes
_OVERRIDE_REMINDER = 7200.0  # remind user after 2h of manual control

# State
_tracked: dict[int, dict] = {}
_known_ids: set[int] = set()
_naked_since: dict[int, float] = {}  # pos_id -> monotonic timestamp
_settings: dict = {}
_running = False
_monitor_task: asyncio.Task | None = None
_capital_cache: Decimal = Decimal(0)
_capital_cache_at: float = 0.0
_CAPITAL_CACHE_TTL = 60.0


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
        "tighten_after": Decimal(s.get("trailing_tighten_after", str(_DEF_TIGHTEN_AFTER))),
    }


async def _get_capital() -> Decimal:
    global _capital_cache, _capital_cache_at
    now = _time.monotonic()
    if _capital_cache > 0 and now - _capital_cache_at < _CAPITAL_CACHE_TTL:
        return _capital_cache
    try:
        async with async_session() as session:
            latest_sub = select(func.max(Balance.snapshot_at)).scalar_subquery()
            result = await session.execute(
                select(func.sum(Balance.usd_value).label("total"))
                .where(Balance.snapshot_at == latest_sub, Balance.usd_value.isnot(None))
            )
            total = result.scalar() or Decimal(0)
        if total > 0:
            _capital_cache = total
            _capital_cache_at = now
        return _capital_cache
    except Exception:
        log.error("trailing_get_capital_failed", exc_info=True)
        return _capital_cache


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

        age_secs = (datetime.now(timezone.utc) - pos.opened_at.replace(tzinfo=timezone.utc)).total_seconds()
        _tracked[pos.id] = {
            "auto_sl": sl_decimal,
            "auto_tp": Decimal(tp_str),
            "oco_list_id": pos.oco_order_list_id,
            "trailing_active": is_trailing,
            "manual_override": False,
            "moving": False,
            "last_move_at": 0.0,
            "last_step_pct": resumed_pct + _DEF_OFFSET if is_trailing else Decimal(0),
            "initial_at": _time.monotonic() - max(age_secs, 0),
            "last_tighten_at": _time.monotonic(),  # cooldown after restart
        }
        log.info("trailing_resumed", symbol=pos.symbol, pos_id=pos.id,
                 sl=sl_str, tp=tp_str, has_oco=bool(pos.oco_order_list_id),
                 age_hours=round(age_secs / 3600, 1))


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
                    log.info("trailing_position_naked", symbol=pos.symbol, pos_id=pid)
                elif _time.monotonic() - _naked_since[pid] > _NAKED_GRACE:
                    _naked_since.pop(pid)
                    asyncio.create_task(_recover_naked_position(pid))

            # Tighten stale/ranging positions OR trailing-stalled positions
            tighten_hours = float(_settings.get("tighten_after", _DEF_TIGHTEN_AFTER))
            if tighten_hours > 0:
                tighten_secs = tighten_hours * 3600
                now = _time.monotonic()
                for pid, tracking in list(_tracked.items()):
                    if tracking.get("manual_override"):
                        continue
                    if tracking.get("moving"):
                        continue
                    # Stall reference: last trail move for trailing-active,
                    # initial OCO placement for pre-activation
                    if tracking.get("trailing_active"):
                        ref_time = tracking.get("last_move_at", 0.0)
                        if not ref_time:
                            continue
                    else:
                        ref_time = tracking.get("initial_at", now)
                    if now - ref_time < tighten_secs:
                        continue
                    last_tighten = tracking.get("last_tighten_at", 0.0)
                    if last_tighten and now - last_tighten < _TIGHTEN_INTERVAL:
                        continue
                    pos = active.get(pid)
                    if pos:
                        tracking["moving"] = True
                        asyncio.create_task(_tighten_stale_position(pos, tracking))

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
        analysis = await market_analyzer.ensure_analysis(pos.symbol)
    key_levels = analysis.get("key_levels", []) if analysis else []

    sl_price, tp_price = _find_initial_sl_tp(key_levels, pos.entry_price, pos.side) if key_levels else (None, None)

    # Check and adjust R:R when we have level-based SL/TP
    min_rr = _settings.get("min_rr", _DEF_MIN_RR)
    if sl_price and tp_price:
        rr = _compute_rr(pos.entry_price, sl_price, tp_price, pos.side)
        if rr < min_rr:
            sl_price, tp_price = _adjust_for_rr(
                key_levels, pos.entry_price, pos.side, min_rr,
            )

    # Fallback: percentage-based SL/TP when key levels insufficient
    sl_price, tp_price = _ensure_sl_tp(
        pos.entry_price, pos.side, sl_price, tp_price, min_rr,
    )

    # Capital-aware: cap SL distance to max risk % of capital
    sl_price, tp_price = await _cap_sl_to_capital(
        pos.entry_price, pos.side, pos.quantity, sl_price, tp_price, min_rr,
    )

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
            "initial_at": _time.monotonic(),
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
        analysis = await market_analyzer.ensure_analysis(pos.symbol)
    key_levels = analysis.get("key_levels", []) if analysis else []

    activation = _settings.get("activation", _DEF_ACTIVATION)
    min_rr = _settings.get("min_rr", _DEF_MIN_RR)

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
        sl_price, tp_price = (None, None)
        if key_levels:
            sl_price, tp_price = _find_initial_sl_tp(key_levels, entry, pos.side)
            if sl_price and tp_price:
                rr = _compute_rr(entry, sl_price, tp_price, pos.side)
                if rr < min_rr:
                    sl_price, tp_price = _adjust_for_rr(
                        key_levels, entry, pos.side, min_rr,
                    )

        # Fallback: percentage-based SL/TP when key levels insufficient
        sl_price, tp_price = _ensure_sl_tp(entry, pos.side, sl_price, tp_price, min_rr)

        # Capital-aware: cap SL distance to max risk % of capital
        sl_price, tp_price = await _cap_sl_to_capital(
            entry, pos.side, pos.quantity, sl_price, tp_price, min_rr,
        )

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
            "initial_at": _time.monotonic(),
        }
        log.info("trailing_naked_recovery", symbol=pos.symbol,
                 sl=str(sl_price), tp=str(tp_price),
                 trailing=trailing_active, gain=f"{gain_pct:.2f}%")
    except Exception:
        log.error("trailing_naked_recovery_failed", symbol=pos.symbol, exc_info=True)


async def _tighten_stale_position(pos, tracking):
    """Re-evaluate SL/TP for stale positions — ranging or trailing-stalled."""
    tracking["last_tighten_at"] = _time.monotonic()
    tracking["moving"] = True
    try:
        current = pos.current_price
        if not current or current <= 0:
            return

        # Skip if price hasn't moved since last tighten
        last_price = tracking.get("last_tighten_price")
        if last_price and abs(current - last_price) / last_price < Decimal("0.001"):
            return
        tracking["last_tighten_price"] = current

        entry = pos.entry_price
        old_sl = tracking["auto_sl"]
        old_tp = tracking["auto_tp"]
        is_stalled = tracking.get("trailing_active", False)

        analysis = market_analyzer.get_analysis(pos.symbol)
        key_levels = analysis.get("key_levels", []) if analysis else []

        new_sl = None
        new_tp = None

        if key_levels:
            if is_stalled:
                # Trailing-stalled: snap SL to nearest level, TP to next resistance
                offset = _settings.get("offset", _DEF_OFFSET)
                new_sl = _find_trailing_sl_level(
                    key_levels, current, pos.side, min_dist_pct=offset,
                )
                new_tp = _find_next_resistance(key_levels, current, pos.side)
            else:
                # Pre-activation ranging: find levels around current price
                new_sl, new_tp = _find_initial_sl_tp(key_levels, current, pos.side)

        # Fallback: percentage-based gap reduction (no key levels or no match)
        if not new_sl:
            if pos.side == "LONG":
                gap = current - old_sl
            else:
                gap = old_sl - current
            if gap > 0:
                reduction = gap * _TIGHTEN_GAP_REDUCTION
                new_sl = old_sl + reduction if pos.side == "LONG" else old_sl - reduction

        if not new_tp:
            if pos.side == "LONG":
                gap = old_tp - current
            else:
                gap = current - old_tp
            if gap > 0:
                reduction = gap * _TIGHTEN_GAP_REDUCTION
                new_tp = old_tp - reduction if pos.side == "LONG" else old_tp + reduction

        if not new_sl and not new_tp:
            return

        new_sl = new_sl or old_sl
        new_tp = new_tp or old_tp

        # TP must be in profit territory
        if pos.side == "LONG" and new_tp <= entry:
            new_tp = old_tp
        if pos.side == "SHORT" and new_tp >= entry:
            new_tp = old_tp

        # Check R:R from entry (relaxed threshold)
        rr = _compute_rr(entry, new_sl, new_tp, pos.side)
        if rr < _TIGHTEN_MIN_RR:
            return

        # Only tighten — never widen
        if pos.side == "LONG":
            sl_tighter = new_sl > old_sl
            tp_tighter = new_tp < old_tp
        else:
            sl_tighter = new_sl < old_sl
            tp_tighter = new_tp > old_tp

        if not sl_tighter and not tp_tighter:
            return

        final_sl = round_price(pos.symbol, new_sl if sl_tighter else old_sl)
        final_tp = round_price(pos.symbol, new_tp if tp_tighter else old_tp)

        if final_sl == old_sl and final_tp == old_tp:
            return

        pos = _get_pos(pos.id)
        if not pos:
            return

        result = await order_manager.place_oco(pos, final_tp, final_sl, silent=True)
        oco_id = str(result.get("orderListId", ""))

        tracking.update({
            "auto_sl": final_sl,
            "auto_tp": final_tp,
            "oco_list_id": oco_id,
            "last_move_at": _time.monotonic(),
        })

        if pos.side == "LONG":
            gain_pct = (current - entry) / entry * 100
        else:
            gain_pct = (entry - current) / entry * 100
        sign = "+" if gain_pct >= 0 else ""

        reason = "stagnation" if is_stalled else "range"
        log.info("trailing_tightened", symbol=pos.symbol,
                 old_sl=str(old_sl), new_sl=str(final_sl),
                 old_tp=str(old_tp), new_tp=str(final_tp),
                 reason=reason)

        sl_changed = f"SL : {old_sl} \u2192 {final_sl}" if sl_tighter else f"SL : {final_sl} (inchang\u00e9)"
        tp_changed = f"TP : {old_tp} \u2192 {final_tp}" if tp_tighter else f"TP : {final_tp} (inchang\u00e9)"
        emoji = "\u23f3" if is_stalled else "\U0001f527"
        label = "trail en pause" if is_stalled else "range"
        asyncio.create_task(telegram_notifier.notify(
            f"{emoji} <b>{pos.symbol} {pos.side}</b> \u2014 SL/TP resserr\u00e9s ({label})\n"
            f"{sl_changed}\n{tp_changed}\n"
            f"Gain : {sign}{gain_pct:.2f}%\n"
            f"Le trailing reprendra si le prix progresse."
        ))
    except Exception:
        log.error("trailing_tighten_failed", symbol=pos.symbol, exc_info=True)
        await _retry_oco_or_fallback_sl(pos, tracking)
    finally:
        tracking["moving"] = False


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
        tracking["moving"] = True
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
            # Breakeven: cover real entry fees + estimated exit fees (0.1%)
            qty = pos.quantity
            entry_fees = pos.entry_fees_usd or Decimal("0")
            exit_fee_rate = Decimal("0.001")
            # SL must recoup: entry_fees + exit_fees_at_sl
            # For LONG: (sl - entry) * qty = entry_fees + sl * qty * exit_fee_rate
            #   sl * qty - sl * qty * exit_fee_rate = entry * qty + entry_fees
            #   sl = (entry * qty + entry_fees) / (qty * (1 - exit_fee_rate))
            # For SHORT: (entry - sl) * qty = entry_fees + sl * qty * exit_fee_rate
            #   sl = (entry * qty - entry_fees) / (qty * (1 + exit_fee_rate))
            buffer = Decimal("5")  # $5 net profit cushion above true breakeven
            if pos.side == "LONG":
                new_sl_price = (entry * qty + entry_fees + buffer) / (qty * (1 - exit_fee_rate))
            else:
                new_sl_price = (entry * qty - entry_fees - buffer) / (qty * (1 + exit_fee_rate))

            # Capital-aware floor: lock portion of gain when significant vs capital
            capital = await _get_capital()
            if capital > 0:
                if pos.side == "LONG":
                    gain_usd = (current_price - entry) * qty
                else:
                    gain_usd = (entry - current_price) * qty
                threshold = capital * _CAP_LOCK_THRESHOLD / 100
                if gain_usd > threshold:
                    lock_usd = threshold + (gain_usd - threshold) * _CAP_LOCK_RATIO
                    if pos.side == "LONG":
                        cap_sl = entry + lock_usd / qty
                    else:
                        cap_sl = entry - lock_usd / qty
                    if (pos.side == "LONG" and cap_sl > new_sl_price) or \
                       (pos.side == "SHORT" and cap_sl < new_sl_price):
                        new_sl_price = cap_sl
                        log.info("trailing_capital_lock",
                                 symbol=pos.symbol, gain_usd=f"{gain_usd:.0f}",
                                 lock_usd=f"{lock_usd:.0f}", capital=f"{capital:.0f}")
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

        # --- Compute TP ---
        new_tp = tracking["auto_tp"]
        candidate = _find_next_resistance(key_levels, current_price, pos.side)
        if candidate:
            new_tp = candidate
        else:
            # No resistance found: push TP to maintain R:R from current price
            risk = abs(current_price - new_sl_price)
            min_rr = _settings.get("min_rr", _DEF_MIN_RR)
            if pos.side == "LONG":
                fallback_tp = current_price + risk * min_rr
            else:
                fallback_tp = current_price - risk * min_rr
            # Only push forward, never backward
            if (pos.side == "LONG" and fallback_tp > new_tp) or \
               (pos.side == "SHORT" and fallback_tp < new_tp):
                new_tp = fallback_tp

        # Re-fetch position for fresh state
        pos = _get_pos(pos.id)
        if not pos:
            return

        new_sl_price = round_price(pos.symbol, new_sl_price)
        new_tp = round_price(pos.symbol, new_tp)

        # Nothing changed: skip useless OCO replace
        if new_sl_price == tracking["auto_sl"] and new_tp == tracking["auto_tp"]:
            tracking["last_step_pct"] = gain_pct
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


# ── Capital-aware helpers ─────────────────────────────────────

async def _cap_sl_to_capital(entry, side, qty, sl, tp, min_rr):
    """Cap SL distance so max loss doesn't exceed _CAP_MAX_RISK_PCT of capital."""
    capital = await _get_capital()
    if capital <= 0:
        return sl, tp
    max_loss = capital * _CAP_MAX_RISK_PCT / 100
    if side == "LONG":
        loss_at_sl = (entry - sl) * qty
        if loss_at_sl > max_loss:
            sl = entry - max_loss / qty
            risk = entry - sl
            tp = min(tp, entry + risk * min_rr)
            log.info("trailing_capital_cap_sl", max_loss=f"{max_loss:.0f}",
                     capped_loss=f"{(entry - sl) * qty:.0f}")
    else:
        loss_at_sl = (sl - entry) * qty
        if loss_at_sl > max_loss:
            sl = entry + max_loss / qty
            risk = sl - entry
            tp = max(tp, entry - risk * min_rr)
            log.info("trailing_capital_cap_sl", max_loss=f"{max_loss:.0f}",
                     capped_loss=f"{(sl - entry) * qty:.0f}")
    return sl, tp


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


def _ensure_sl_tp(entry, side, sl, tp, min_rr):
    """Guarantee SL and TP are set, using percentage fallback if needed."""
    if not sl:
        pct = _DEF_FALLBACK_SL_PCT / 100
        sl = entry * (1 - pct) if side == "LONG" else entry * (1 + pct)
    if not tp:
        risk = abs(entry - sl)
        tp = entry + risk * min_rr if side == "LONG" else entry - risk * min_rr
    return sl, tp


def _find_initial_sl_tp(key_levels, entry_price, side):
    prices = _parse_levels(key_levels)
    if not prices:
        return None, None

    min_dist = Decimal("0.8")  # skip levels < 0.8% away (covers fees + noise)
    max_dist = _DEF_MAX_SL_PCT  # cap SL distance to limit losses

    if side == "LONG":
        sl = None
        for p in reversed(prices):
            if p < entry_price:
                dist = (entry_price - p) / entry_price * 100
                if dist >= min_dist:
                    sl = p
                    break
        # Cap SL if level is too far
        if sl:
            dist = (entry_price - sl) / entry_price * 100
            if dist > max_dist:
                sl = entry_price * (1 - max_dist / 100)
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
        # Cap SL if level is too far
        if sl:
            dist = (sl - entry_price) / entry_price * 100
            if dist > max_dist:
                sl = entry_price * (1 + max_dist / 100)
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
    min_dist = Decimal("0.8")

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
