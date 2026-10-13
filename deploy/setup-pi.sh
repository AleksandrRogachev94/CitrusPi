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

# 3. Preserve microSD card with log2ram (redirects /var/log to RAM)
if ! dpkg -s log2ram >/dev/null 2>&1; then
    echo "==> Setting up log2ram to protect microSD card..."
    echo "deb [signed-by=/usr/share/keyrings/azlux-archive-keyring.gpg] http://packages.azlux.fr/debian/ bookworm main" | sudo tee /etc/apt/sources.list.d/azlux.list >/dev/null
    sudo wget -qO /usr/share/keyrings/azlux-archive-keyring.gpg https://azlux.fr/repo.gpg || true
    sudo apt-get update >/dev/null 2>&1 && sudo apt-get install -y log2ram || echo "Note: log2ram skipped (can be installed manually)."
fi

# 4. Create virtual environment with system site-packages (for OpenCV access)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "==> Creating Python virtual environment in $SCRIPT_DIR/.venv..."
python3 -m venv --system-site-packages .venv
source .venv/bin/activate

echo "==> Installing Python dependencies..."
pip install --upgrade pip
pip install -e .

# 5. Install & start systemd service
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
echo "  - Check RAM logging:      systemctl status log2ram"
