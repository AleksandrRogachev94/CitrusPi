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

Done. Open **http://citruspi.local:8000** in your browser.

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
```
