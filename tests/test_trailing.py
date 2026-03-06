"""Test script for trailing_manager logic — simulates positions and price moves."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

# Fake Position class matching the real model
class FakePosition:
    def __init__(self, id, symbol, side, entry_price, quantity, market_type="CROSS_MARGIN"):
        self.id = id
        self.symbol = symbol
        self.side = side
        self.entry_price = Decimal(str(entry_price))
        self.quantity = Decimal(str(quantity))
        self.market_type = market_type
        self.current_price = self.entry_price
        self.pnl_usd = Decimal("0")
        self.pnl_pct = Decimal("0")
        self.is_active = True
        self.sl_order_id = None
        self.tp_order_id = None
        self.oco_order_list_id = None
        self.entry_fees_usd = Decimal("0")
        self.opened_at = datetime.now(timezone.utc)
        self.updated_at = None

FAKE_LEVELS = [
    {"price": "74639.74", "type": "R2", "label": "Forte resistance"},
    {"price": "74086.95", "type": "SW_H", "label": "Plafond recent 1"},
    {"price": "73584.71", "type": "SW_H", "label": "Plafond recent 2"},
    {"price": "72763.41", "type": "R1", "label": "Resistance"},
    {"price": "72144.77", "type": "FIB_1618", "label": "Fib Ext 161.8%"},
    {"price": "71708.37", "type": "PP", "label": "Pivot du jour"},
    {"price": "71130.60", "type": "FIB_1272", "label": "Fib Ext 127.2%"},
    {"price": "70540.15", "type": "SW_H", "label": "Plafond recent 3"},
    {"price": "69832.04", "type": "S1", "label": "Support"},
    {"price": "69213.64", "type": "FIB_618", "label": "Fib 61.8%"},
    {"price": "68777.00", "type": "S2", "label": "Fort support"},
    {"price": "68306.66", "type": "SW_H", "label": "Plafond recent 4"},
    {"price": "67737.88", "type": "D_L", "label": "Plus bas du jour"},
    {"price": "67365.32", "type": "SW_L", "label": "Plancher recent 1"},
    {"price": "67000.00", "type": "PSYCH", "label": "Niveau psycho 67 000"},
    {"price": "66514.20", "type": "SW_L", "label": "Plancher recent 2"},
    {"price": "66000.00", "type": "PSYCH", "label": "Niveau psycho 66 000"},
    {"price": "65613.70", "type": "SW_L", "label": "Plancher recent 3"},
    {"price": "65081.51", "type": "SW_L", "label": "Plancher recent 4"},
]


def test_find_initial_sl_tp():
    """Test SL/TP selection from key levels."""
    from backend.trading.trailing_manager import _find_initial_sl_tp, _compute_rr

    print("=" * 60)
    print("TEST 1: Initial SL/TP from key levels")
    print("=" * 60)

    # LONG at 68043
    entry = Decimal("68043")
    sl, tp = _find_initial_sl_tp(FAKE_LEVELS, entry, "LONG")
    rr = _compute_rr(entry, sl, tp, "LONG") if sl and tp else Decimal(0)
    print(f"\nLONG entry: {entry}")
    print(f"  SL: {sl} ({(entry - sl) / entry * 100:.2f}% below)" if sl else "  SL: None")
    print(f"  TP: {tp} ({(tp - entry) / entry * 100:.2f}% above)" if tp else "  TP: None")
    print(f"  R:R: 1:{rr:.1f}")
    assert sl is not None, "SL should be found"
    assert tp is not None, "TP should be found"
    assert sl < entry, "SL should be below entry for LONG"
    assert tp > entry, "TP should be above entry for LONG"
    print("  PASS")

    # LONG on a support level (entry = 67365)
    entry2 = Decimal("67365")
    sl2, tp2 = _find_initial_sl_tp(FAKE_LEVELS, entry2, "LONG")
    rr2 = _compute_rr(entry2, sl2, tp2, "LONG") if sl2 and tp2 else Decimal(0)
    print(f"\nLONG entry on support: {entry2}")
    print(f"  SL: {sl2} ({(entry2 - sl2) / entry2 * 100:.2f}% below)" if sl2 else "  SL: None")
    print(f"  TP: {tp2} ({(tp2 - entry2) / entry2 * 100:.2f}% above)" if tp2 else "  TP: None")
    print(f"  R:R: 1:{rr2:.1f}")
    assert sl2 < entry2, "SL should skip entry level and use next below"
    print("  PASS")

    # SHORT at 72000
    entry3 = Decimal("72000")
    sl3, tp3 = _find_initial_sl_tp(FAKE_LEVELS, entry3, "SHORT")
    rr3 = _compute_rr(entry3, sl3, tp3, "SHORT") if sl3 and tp3 else Decimal(0)
    print(f"\nSHORT entry: {entry3}")
    print(f"  SL: {sl3} ({(sl3 - entry3) / entry3 * 100:.2f}% above)" if sl3 else "  SL: None")
    print(f"  TP: {tp3} ({(entry3 - tp3) / entry3 * 100:.2f}% below)" if tp3 else "  TP: None")
    print(f"  R:R: 1:{rr3:.1f}")
    assert sl3 > entry3, "SL should be above entry for SHORT"
    assert tp3 < entry3, "TP should be below entry for SHORT"
    print("  PASS")


def test_adjust_for_rr():
    """Test R:R adjustment."""
    from backend.trading.trailing_manager import _adjust_for_rr, _compute_rr

    print("\n" + "=" * 60)
    print("TEST 2: R:R adjustment")
    print("=" * 60)

    # Entry very close to a support — initial R:R might be low
    entry = Decimal("68400")
    min_rr = Decimal("1.5")
    sl, tp = _adjust_for_rr(FAKE_LEVELS, entry, "LONG", min_rr)
    if sl and tp:
        rr = _compute_rr(entry, sl, tp, "LONG")
        print(f"\nAdjusted LONG at {entry} for min R:R {min_rr}")
        print(f"  SL: {sl}")
        print(f"  TP: {tp}")
        print(f"  R:R: 1:{rr:.1f}")
        assert rr >= min_rr, f"R:R {rr} should be >= {min_rr}"
        print("  PASS")
    else:
        print(f"\n  No valid SL/TP found for R:R >= {min_rr} (acceptable)")


def test_find_next_resistance():
    """Test finding next resistance above current price."""
    from backend.trading.trailing_manager import _find_next_resistance

    print("\n" + "=" * 60)
    print("TEST 3: Next resistance lookup")
    print("=" * 60)

    prices = [
        Decimal("69000"),
        Decimal("70000"),
        Decimal("71500"),
        Decimal("73000"),
    ]
    for p in prices:
        r = _find_next_resistance(FAKE_LEVELS, p, "LONG")
        print(f"  Price {p} -> next resistance: {r}")
        if r:
            assert r > p, "Resistance should be above price"
    print("  PASS")


def test_trailing_simulation():
    """Simulate price movement with step-based trailing."""
    from backend.trading.trailing_manager import (
        _find_initial_sl_tp, _compute_rr, _find_next_resistance,
    )

    print("\n" + "=" * 60)
    print("TEST 4: Full trailing simulation (step-based)")
    print("=" * 60)

    entry = Decimal("68043")
    side = "LONG"
    activation = Decimal("0.8")
    breakeven = Decimal("0.2")
    step = Decimal("0.3")
    offset = Decimal("0.4")
    tp_guard = Decimal("0.2")

    sl, tp = _find_initial_sl_tp(FAKE_LEVELS, entry, side)
    print(f"\nEntry: {entry}")
    print(f"Initial OCO: SL {sl} | TP {tp}")
    print(f"Settings: activation={activation}% breakeven={breakeven}% step={step}% offset={offset}%")

    current_sl = sl
    current_tp = tp
    move_count = 0
    trailing_active = False
    last_step_pct = Decimal(0)

    # Simulate: up to +0.9%, retrace (stays above BE SL), then pump
    price_path = [
        68043, 68200, 68400, 68600,  # warming up
        68650,                        # +0.89% -> activation! SL to BE +0.2%
        68400, 68300, 68500,          # retrace but stays above SL (68179)
        68700, 68900, 69100,          # climbing again
        69300, 69500, 69800,          # more up (step moves start)
        70000, 70200, 70500,          # climbing
        71000, 71500, 72000, 72500,   # big pump
        72000, 71800, 71500,          # retracement -> SL triggers
    ]

    print(f"\n{'Price':>10} {'Gain%':>7} {'SL':>10} {'TP':>10} {'Action'}")
    print("-" * 70)

    for price in price_path:
        price = Decimal(str(price))
        gain_pct = (price - entry) / entry * 100

        action = ""

        if gain_pct >= activation:
            should_move = False
            new_sl_pct = None

            if not trailing_active:
                # First move: breakeven
                new_sl_pct = breakeven
                should_move = True
            elif gain_pct >= last_step_pct + step:
                # Step move
                new_sl_pct = gain_pct - offset
                should_move = True

            # TP guard
            if not should_move and current_tp:
                tp_dist = (current_tp - price) / price * 100
                if Decimal(0) < tp_dist <= tp_guard:
                    new_sl_pct = gain_pct - offset
                    should_move = True

            if should_move and new_sl_pct is not None:
                new_sl_price = entry * (1 + new_sl_pct / 100)
                if new_sl_price > current_sl:
                    current_sl = new_sl_price
                    trailing_active = True
                    last_step_pct = gain_pct
                    new_tp = _find_next_resistance(FAKE_LEVELS, price, side)
                    if new_tp and new_tp != current_tp:
                        current_tp = new_tp
                    move_count += 1
                    kind = "BE" if move_count == 1 else "STEP"
                    action = f"MOVE #{move_count} [{kind}] SL->{new_sl_pct:.2f}%"

        # Check if SL triggered
        if price <= current_sl:
            gain_at_exit = (current_sl - entry) / entry * 100
            action = f"SL TRIGGERED -- exit at +{gain_at_exit:.2f}%"

        print(f"{price:>10} {gain_pct:>6.2f}% {current_sl:>10.2f} {current_tp:>10.2f} {action}")

        if "TRIGGERED" in action:
            break

    print(f"\nTotal OCO moves: {move_count}")
    final_gain = (current_sl - entry) / entry * 100
    print(f"Locked profit at SL: +{final_gain:.2f}%")
    assert move_count >= 2, "Should have at least BE + one step move"
    print("  PASS")


def test_trailing_short():
    """Simulate SHORT trailing with step-based logic."""
    from backend.trading.trailing_manager import (
        _find_initial_sl_tp, _compute_rr, _find_next_resistance,
    )

    print("\n" + "=" * 60)
    print("TEST 4b: Full trailing simulation (SHORT, step-based)")
    print("=" * 60)

    entry = Decimal("72000")
    side = "SHORT"
    activation = Decimal("0.8")
    breakeven = Decimal("0.2")
    step = Decimal("0.3")
    offset = Decimal("0.4")

    sl, tp = _find_initial_sl_tp(FAKE_LEVELS, entry, side)
    print(f"\nEntry SHORT: {entry}")
    print(f"Initial OCO: SL {sl} (above) | TP {tp} (below)")

    current_sl = sl
    current_tp = tp
    move_count = 0
    trailing_active = False
    last_step_pct = Decimal(0)

    price_path = [
        72000, 71800, 71600, 71400,  # warming up
        71200, 71000, 70800, 70600,  # approaching activation
        70400, 70200, 70000, 69800,  # trailing active
        69600, 69400, 69200, 69000,  # dumping
        68500, 68000, 67500,         # big dump
        67800, 68200, 68500, 69000,  # bounce up
    ]

    print(f"\n{'Price':>10} {'Gain%':>7} {'SL':>10} {'TP':>10} {'Action'}")
    print("-" * 70)

    for price in price_path:
        price = Decimal(str(price))
        gain_pct = (entry - price) / entry * 100

        action = ""

        if gain_pct >= activation:
            should_move = False
            new_sl_pct = None

            if not trailing_active:
                new_sl_pct = breakeven
                should_move = True
            elif gain_pct >= last_step_pct + step:
                new_sl_pct = gain_pct - offset
                should_move = True

            if should_move and new_sl_pct is not None:
                new_sl_price = entry * (1 - new_sl_pct / 100)
                if new_sl_price < current_sl:
                    current_sl = new_sl_price
                    trailing_active = True
                    last_step_pct = gain_pct
                    new_tp = _find_next_resistance(FAKE_LEVELS, price, side)
                    if new_tp and new_tp != current_tp:
                        current_tp = new_tp
                    move_count += 1
                    kind = "BE" if move_count == 1 else "STEP"
                    action = f"MOVE #{move_count} [{kind}] SL->{new_sl_pct:.2f}%"

        if price >= current_sl:
            gain_at_exit = (entry - current_sl) / entry * 100
            action = f"SL TRIGGERED -- exit at +{gain_at_exit:.2f}%"

        print(f"{price:>10} {gain_pct:>6.2f}% {current_sl:>10.2f} {current_tp:>10.2f} {action}")

        if "TRIGGERED" in action:
            break

    print(f"\nTotal OCO moves: {move_count}")
    final_gain = (entry - current_sl) / entry * 100
    print(f"Locked profit at SL: +{final_gain:.2f}%")
    assert move_count >= 2, "Should have at least BE + one step move"
    assert final_gain > 0, "Should lock positive profit"
    print("  PASS")


def test_manual_override():
    """Test manual override detection."""
    from backend.trading.trailing_manager import _check_manual_override

    print("\n" + "=" * 60)
    print("TEST 5: Manual override detection")
    print("=" * 60)

    pos = FakePosition(1, "BTCUSDC", "LONG", 68043, 0.05)
    tracking = {
        "oco_list_id": "12345",
        "manual_override": False,
        "auto_sl": Decimal("67365"),
        "auto_tp": Decimal("69832"),
    }

    # Case 1: user places different OCO
    pos.oco_order_list_id = "99999"
    _check_manual_override(pos, tracking)
    assert tracking["manual_override"] is True, "Should detect different OCO"
    print("  Different OCO ->override detected: PASS")

    # Case 2: user places individual SL
    tracking["manual_override"] = False
    pos.oco_order_list_id = None
    pos.sl_order_id = "111"
    _check_manual_override(pos, tracking)
    assert tracking["manual_override"] is True, "Should detect individual orders"
    print("  Individual SL ->override detected: PASS")

    # Case 3: user removes all orders
    tracking["manual_override"] = False
    pos.sl_order_id = None
    pos.tp_order_id = None
    _check_manual_override(pos, tracking)
    assert tracking["manual_override"] is True, "Should detect orders removed"
    print("  Orders removed ->override detected: PASS")


if __name__ == "__main__":
    test_find_initial_sl_tp()
    test_adjust_for_rr()
    test_find_next_resistance()
    test_trailing_simulation()
    test_trailing_short()
    test_manual_override()
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)
