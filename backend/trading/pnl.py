from decimal import Decimal

ESTIMATED_EXIT_FEE_RATE = Decimal("0.001")  # 0.1% taker fee


def gross_pnl(
    side: str, entry_price: Decimal, current_price: Decimal, quantity: Decimal,
) -> Decimal:
    if side == "LONG":
        return (current_price - entry_price) * quantity
    return (entry_price - current_price) * quantity


def total_fees(
    entry_fees_usd: Decimal | None, exit_fees_usd: Decimal | None,
) -> Decimal:
    return (entry_fees_usd or Decimal("0")) + (exit_fees_usd or Decimal("0"))


def estimated_exit_fees(quantity: Decimal, price: Decimal) -> Decimal:
    return quantity * price * ESTIMATED_EXIT_FEE_RATE


def unrealized_pnl(
    side: str,
    entry_price: Decimal,
    current_price: Decimal,
    quantity: Decimal,
    entry_fees_usd: Decimal | None,
) -> tuple[Decimal, Decimal]:
    """Returns (pnl_usd, pnl_pct) for an active position."""
    if entry_price <= 0:
        return Decimal("0"), Decimal("0")
    gross = gross_pnl(side, entry_price, current_price, quantity)
    fees_in = entry_fees_usd or Decimal("0")
    fees_out = estimated_exit_fees(quantity, current_price)
    net = gross - fees_in - fees_out
    cost = entry_price * quantity
    pct = (net / cost * 100) if cost > 0 else Decimal("0")
    return net, pct


def net_realized_pnl(
    realized_pnl: Decimal,
    entry_fees_usd: Decimal | None,
    exit_fees_usd: Decimal | None,
) -> Decimal:
    return realized_pnl - total_fees(entry_fees_usd, exit_fees_usd)


def realized_pnl_pct(
    realized_pnl: Decimal,
    entry_fees_usd: Decimal | None,
    exit_fees_usd: Decimal | None,
    entry_quantity: Decimal | None,
    quantity: Decimal,
    entry_price: Decimal,
) -> Decimal:
    entry_cost = (entry_quantity or quantity) * entry_price
    net = net_realized_pnl(realized_pnl, entry_fees_usd, exit_fees_usd)
    return (net / entry_cost * 100) if entry_cost > 0 else Decimal("0")


def is_win(
    realized_pnl: Decimal,
    entry_fees_usd: Decimal | None,
    exit_fees_usd: Decimal | None,
) -> bool:
    return net_realized_pnl(realized_pnl, entry_fees_usd, exit_fees_usd) > 0
