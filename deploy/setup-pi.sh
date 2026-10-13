#!/usr/bin/env bash
set -e

echo "🍊 Setting up CitrusPi on Raspberry Pi..."

# 1. Enable I2C interface via raspi-config
echo "==> Enabling I2C hardware interface..."
sudo raspi-config nonint do_i2c 0 || true

# 2. System dependencies
# Note: Installing python3-opencv via apt provides pre-compiled ARM binaries.
# This prevents pip from attempting to compile OpenCV from source on the Pi Zero 2W.
echo "==> Installing system packages..."
sudo apt-get update
sudo apt-get install -y \
    python3-venv \
    python3-pip \
    python3-opencv \
    i2c-tools \
    v4l-utils

# 3. Create virtual environment with system site-packages (for OpenCV access)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "==> Creating Python virtual environment in $SCRIPT_DIR/.venv..."
python3 -m venv --system-site-packages .venv
source .venv/bin/activate

echo "==> Installing Python dependencies..."
pip install --upgrade pip
pip install -e .

# 4. Install & start systemd service
echo "==> Installing systemd background service..."
sudo cp deploy/citruspi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable citruspi
sudo systemctl restart citruspi

echo ""
echo "🍊 CitrusPi setup complete!"
echo "Dashboard is running at: http://$(hostname -I | awk '{print $1}'):8000"
echo ""
echo "Helpful verification commands:"
echo "  - Check service status:   sudo systemctl status citruspi"
echo "  - View live logs:         sudo journalctl -u citruspi -f"
echo "  - Check I2C sensors:      sudo i2cdetect -y 1"
echo "  - Check USB camera:       v4l2-ctl --list-devices"
