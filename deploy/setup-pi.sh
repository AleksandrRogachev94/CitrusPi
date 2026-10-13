#!/usr/bin/env bash
set -e

echo "🍊 Setting up CitrusPi on Raspberry Pi..."

# 1. Enable I2C interface
echo "==> Enabling I2C..."
sudo raspi-config nonint do_i2c 0 || true

# 2. System dependencies
echo "==> Installing system packages..."
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip i2c-tools

# 3. Create virtual environment & install package
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "==> Setting up Python virtual environment in $SCRIPT_DIR/.venv..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .

# 4. Install systemd service
echo "==> Installing systemd service..."
sudo cp deploy/citruspi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable citruspi
sudo systemctl restart citruspi

echo ""
echo "🍊 CitrusPi setup complete!"
echo "Dashboard is running at http://$(hostname -I | awk '{print $1}'):8000"
echo "Check logs anytime with: sudo journalctl -u citruspi -f"
