#!/usr/bin/env bash
set -e

echo "🍊 Setting up CitrusPi on Raspberry Pi..."

# 1. Enable hardware interfaces via raspi-config
echo "==> Enabling I2C hardware interface..."
sudo raspi-config nonint do_i2c 0 || true

# Enable 1-Wire interface for DS18B20 root-zone temperature probe.
# Note: Probe wires to GPIO4 (Pin 7) with a 4.7kΩ pull-up between data and 3.3V (Pin 1).
# A reboot is needed after enabling 1-Wire for the w1-gpio kernel overlay to activate.
echo "==> Enabling 1-Wire hardware interface (DS18B20)..."
sudo raspi-config nonint do_onewire 0 || true

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

# 5. Download offline web vendor assets if not present
VENDOR_DIR="$SCRIPT_DIR/src/citruspi/static/vendor"
mkdir -p "$VENDOR_DIR"
if [ ! -f "$VENDOR_DIR/htmx.min.js" ]; then
    echo "==> Downloading offline vendor asset: htmx.min.js..."
    curl -fsSL https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js -o "$VENDOR_DIR/htmx.min.js" || true
fi
if [ ! -f "$VENDOR_DIR/chart.umd.min.js" ]; then
    echo "==> Downloading offline vendor asset: chart.umd.min.js..."
    curl -fsSL https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js -o "$VENDOR_DIR/chart.umd.min.js" || true
fi
if [ ! -f "$VENDOR_DIR/chartjs-adapter-date-fns.bundle.min.js" ]; then
    echo "==> Downloading offline vendor asset: chartjs-adapter-date-fns.bundle.min.js..."
    curl -fsSL https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js -o "$VENDOR_DIR/chartjs-adapter-date-fns.bundle.min.js" || true
fi

# 5. Install & start systemd service (dynamically configure user and path)
CURRENT_USER="$(id -un)"
echo "==> Configuring systemd service for user '$CURRENT_USER' in '$SCRIPT_DIR'..."
sudo usermod -aG i2c,video "$CURRENT_USER" || true

sed -e "s|User=pi|User=$CURRENT_USER|g" \
    -e "s|WorkingDirectory=/home/pi/citruspi|WorkingDirectory=$SCRIPT_DIR|g" \
    -e "s|/home/pi/citruspi/.venv/bin/python|$SCRIPT_DIR/.venv/bin/python|g" \
    deploy/citruspi.service | sudo tee /etc/systemd/system/citruspi.service > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable citruspi
sudo systemctl restart citruspi

# Secure config.yaml permissions if it exists
if [ -f config.yaml ]; then
    chmod 600 config.yaml
fi

echo ""
echo "🍊 CitrusPi setup complete!"
echo "Dashboard is running at: http://$(hostname -I | awk '{print $1}') (or http://citruspi.local)"
echo ""
echo "⚠️  REBOOT REQUIRED: 1-Wire kernel overlay requires a reboot to detect the DS18B20 probe!"
echo "    Run: sudo reboot"
echo ""
echo "🕒 TIMEZONE CHECK: Ensure the Pi system timezone matches your local time (photoperiod schedule):"
echo "    Run: sudo timedatectl set-timezone America/New_York  # (or your local timezone)"
echo ""
echo "Helpful verification commands:"
echo "  - Check service status:   sudo systemctl status citruspi"
echo "  - View live logs:         sudo journalctl -u citruspi -f"
echo "  - Check I2C sensors:      sudo i2cdetect -y 1"
echo "  - Check 1-Wire probe:     ls /sys/bus/w1/devices/ (look for 28-*)"
echo "  - Check USB camera:       v4l2-ctl --list-devices"
echo "  - Check RAM logging:      systemctl status log2ram"
