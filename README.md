# 🍊 CitrusPi

Edge monitor for citrus grow tents. Runs on a Raspberry Pi Zero 2W.
Logs temperature, humidity, and soil moisture every minute, snaps one daily timelapse photo, offers live camera preview, and serves a web dashboard.

---

## Setup on a Fresh Pi (Pi OS Lite)

### 1. Copy code to the Pi (from your Mac)
```bash
rsync -avz --exclude '.venv' . pi@citruspi.local:~/citruspi
```
*(Or on the Pi: `sudo apt update && sudo apt install -y git && git clone <repo> ~/citruspi`)*

### 2. Run Setup
```bash
ssh pi@citruspi.local
cd ~/citruspi && ./deploy/setup-pi.sh
```
*Note: The script automatically enables I2C, installs dependencies, and configures `log2ram` to protect your microSD card from write wear.*

Done. Open **http://citruspi.local:8000** in your browser.

---

## MicroSD Card Protection (`log2ram`)

Raspberry Pi OS writes constant syslog data to disk, which wears out consumer microSD cards over multi-month deployments.

`setup-pi.sh` sets up `log2ram` automatically. To install or verify it manually:

```bash
# Add repo and install log2ram
echo "deb [signed-by=/usr/share/keyrings/azlux-archive-keyring.gpg] http://packages.azlux.fr/debian/ bookworm main" | sudo tee /etc/apt/sources.list.d/azlux.list
sudo wget -qO /usr/share/keyrings/azlux-archive-keyring.gpg https://azlux.fr/repo.gpg
sudo apt update && sudo apt install -y log2ram

# Verify active
systemctl status log2ram
```

---

## Configuration

Copy `config.example.yaml` to `config.yaml` to tweak settings:

```yaml
poll_interval_sec: 60     # Sensor interval (seconds)
daily_capture_hour: 22    # 10 PM daily timelapse photo
daily_capture_minute: 0
stream_fps: 5             # Live preview framerate
port: 8000
```

---

## Useful Commands

```bash
# View live logs
sudo journalctl -u citruspi -f

# Verify I2C sensors (0x76 = BME280, 0x36 = Soil moisture)
sudo i2cdetect -y 1

# Verify USB camera
v4l2-ctl --list-devices

# Check log2ram status
systemctl status log2ram
```
