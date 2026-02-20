#!/usr/bin/env bash
set -euo pipefail

# RootCoin Database Backup Script
# Creates a timestamped copy of the SQLite database
# Usage: bash backup_db.sh
# Cron example (daily at 3am): 0 3 * * * /home/rootcoin/rootcoin/scripts/backup_db.sh

APP_DIR="/home/rootcoin/rootcoin"
DB_FILE="$APP_DIR/data/rootcoin.db"
BACKUP_DIR="$APP_DIR/data/backups"
RETENTION_DAYS=30

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_FILE" ]; then
    echo "Database not found: $DB_FILE"
    exit 1
fi

# Use sqlite3 .backup for a safe copy (no corruption risk)
TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/rootcoin_${TIMESTAMP}.db"

if command -v sqlite3 &>/dev/null; then
    sqlite3 "$DB_FILE" ".backup '$BACKUP_FILE'"
else
    cp "$DB_FILE" "$BACKUP_FILE"
fi

echo "Backup created: $BACKUP_FILE"

# Clean old backups
find "$BACKUP_DIR" -name "rootcoin_*.db" -mtime +$RETENTION_DAYS -delete
echo "Old backups (>$RETENTION_DAYS days) cleaned."
