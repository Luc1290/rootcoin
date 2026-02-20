#!/usr/bin/env bash
set -euo pipefail

# RootCoin Deploy Script
# Run on the VPS to pull latest code and restart the service
# Usage: bash deploy.sh

APP_DIR="/home/rootcoin/rootcoin"

echo "=== RootCoin Deploy ==="

cd "$APP_DIR"

# Pull latest code
echo "[1/4] Pulling latest code..."
git pull

# Update dependencies if requirements changed
echo "[2/4] Updating dependencies..."
source venv/bin/activate
pip install -r requirements.txt --quiet

# Rebuild Tailwind CSS if npx is available
if command -v npx &>/dev/null; then
    echo "[3/4] Rebuilding Tailwind CSS..."
    npx tailwindcss -i frontend/css/tailwind.css -o frontend/css/output.css --minify
else
    echo "[3/4] npx not found, skipping Tailwind build."
fi

# Restart service
echo "[4/4] Restarting service..."
sudo systemctl restart rootcoin

echo ""
echo "=== Deploy complete ==="
echo "Check logs: journalctl -u rootcoin -f"
