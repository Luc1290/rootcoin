#!/usr/bin/env bash
set -euo pipefail

# RootCoin VPS Setup Script
# Run as root on a fresh Ubuntu 22.04+ / Debian 12+ server
# Usage: sudo bash setup_vps.sh

APP_USER="rootcoin"
APP_DIR="/home/$APP_USER/rootcoin"
REPO_URL="git@github.com:YOUR_USERNAME/rootcoin.git"  # <-- UPDATE THIS

echo "=== RootCoin VPS Setup ==="

# 1. System updates
echo "[1/7] Updating system..."
apt-get update && apt-get upgrade -y

# 2. Install Python 3.11+
echo "[2/7] Installing Python..."
apt-get install -y python3 python3-pip python3-venv git curl

PYTHON_VERSION=$(python3 --version | awk '{print $2}')
echo "Python version: $PYTHON_VERSION"

# 3. Create app user
echo "[3/7] Creating user '$APP_USER'..."
if id "$APP_USER" &>/dev/null; then
    echo "User '$APP_USER' already exists, skipping."
else
    useradd -m -s /bin/bash "$APP_USER"
    echo "User '$APP_USER' created."
fi

# 4. Clone repo and setup venv
echo "[4/7] Setting up application..."
sudo -u "$APP_USER" bash <<EOF
cd /home/$APP_USER

if [ -d "rootcoin" ]; then
    echo "Repo already exists, pulling latest..."
    cd rootcoin && git pull
else
    git clone $REPO_URL
    cd rootcoin
fi

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create .env from example if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo "IMPORTANT: Edit /home/$APP_USER/rootcoin/.env with your Binance API keys!"
fi

# Create data directory for SQLite
mkdir -p data
EOF

# 5. Install systemd service
echo "[5/7] Installing systemd service..."
cp "$APP_DIR/scripts/rootcoin.service" /etc/systemd/system/rootcoin.service
systemctl daemon-reload
systemctl enable rootcoin

# 6. Configure firewall (only allow SSH + Tailscale)
echo "[6/7] Configuring firewall..."
if command -v ufw &>/dev/null; then
    ufw allow OpenSSH
    ufw --force enable
    echo "Firewall enabled. Port 8001 accessible only via Tailscale."
fi

# 7. Install Tailscale
echo "[7/7] Installing Tailscale..."
if command -v tailscale &>/dev/null; then
    echo "Tailscale already installed."
else
    curl -fsSL https://tailscale.com/install.sh | sh
    echo "Run 'sudo tailscale up' to authenticate."
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit /home/$APP_USER/rootcoin/.env with your Binance API keys"
echo "  2. Run 'sudo tailscale up' to join your Tailscale network"
echo "  3. Add VPS public IP to Binance API key whitelist"
echo "  4. Start the service: sudo systemctl start rootcoin"
echo "  5. Check logs: journalctl -u rootcoin -f"
echo "  6. Access dashboard: http://<tailscale-ip>:8001"
