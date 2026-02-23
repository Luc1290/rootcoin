"""Import trade cycles from CSV into the positions table."""

import csv
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

CSV_PATH = Path(__file__).parent / "trade_cycles_2026.csv"
DB_PATH = Path(__file__).parent / "data" / "rootcoin.db"


def parse_decimal(val: str) -> Decimal | None:
    if not val or val.strip() == "":
        return None
    try:
        return Decimal(val.strip())
    except InvalidOperation:
        return None


def parse_datetime(val: str) -> str | None:
    if not val or val.strip() == "":
        return None
    return val.strip()


def map_market_type(account_type: str) -> str:
    if account_type == "MARGIN":
        return "CROSS_MARGIN"
    return account_type  # SPOT stays SPOT


def main():
    if not CSV_PATH.exists():
        print(f"CSV not found: {CSV_PATH}")
        sys.exit(1)
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # Load existing positions for dedup (symbol + entry_price + quantity + opened_at)
    cursor.execute("SELECT symbol, entry_price, quantity, opened_at FROM positions")
    existing = set()
    for row in cursor.fetchall():
        existing.add((row[0], str(row[1]), str(row[2]), row[3][:19] if row[3] else ""))

    imported = 0
    skipped_canceled = 0
    skipped_duplicate = 0
    skipped_no_exit = 0

    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = row["status"].strip()
            if status == "canceled":
                skipped_canceled += 1
                continue

            symbol = row["symbol"].strip()
            side = row["position_side"].strip()  # LONG or SHORT
            market_type = map_market_type(row["account_type"].strip())
            entry_price = parse_decimal(row["entry_price"])
            exit_price = parse_decimal(row["exit_price"])
            quantity = parse_decimal(row["quantity"])
            realized_pnl = parse_decimal(row["profit_loss"])
            realized_pnl_pct = parse_decimal(row["profit_loss_percent"])
            opened_at = parse_datetime(row["created_at"])
            updated_at = parse_datetime(row["updated_at"])
            closed_at = parse_datetime(row["completed_at"])

            if entry_price is None or quantity is None:
                print(f"  SKIP (no entry_price/qty): {row['id']}")
                continue

            # Skip cycles with no exit (incomplete data)
            if exit_price is None and realized_pnl is None:
                skipped_no_exit += 1
                continue

            # Dedup check
            key = (symbol, str(entry_price), str(quantity), opened_at[:19] if opened_at else "")
            if key in existing:
                skipped_duplicate += 1
                continue

            is_active = status != "completed"

            cursor.execute(
                """INSERT INTO positions (
                    symbol, side, entry_price, quantity, market_type,
                    current_price, pnl_usd, pnl_pct,
                    opened_at, updated_at,
                    sl_order_id, tp_order_id, oco_order_list_id,
                    entry_fees_usd, entry_quantity,
                    exit_price, exit_fees_usd,
                    realized_pnl, realized_pnl_pct,
                    closed_at, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol, side, str(entry_price), str(quantity), market_type,
                    None, None, None,
                    opened_at, updated_at,
                    None, None, None,
                    "0", str(quantity),
                    str(exit_price) if exit_price else None, "0",
                    str(realized_pnl) if realized_pnl else None,
                    str(realized_pnl_pct) if realized_pnl_pct else None,
                    closed_at, is_active,
                ),
            )
            existing.add(key)
            imported += 1

    conn.commit()

    # Verify
    cursor.execute("SELECT COUNT(*) FROM positions")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM positions WHERE is_active = 0 AND realized_pnl IS NOT NULL")
    closed_with_pnl = cursor.fetchone()[0]

    conn.close()

    print(f"\n=== Import terminé ===")
    print(f"  Importés:          {imported}")
    print(f"  Ignorés (canceled): {skipped_canceled}")
    print(f"  Ignorés (dupl.):   {skipped_duplicate}")
    print(f"  Ignorés (no exit): {skipped_no_exit}")
    print(f"  Total positions DB: {total}")
    print(f"  Fermées avec PnL:  {closed_with_pnl}")


if __name__ == "__main__":
    main()
