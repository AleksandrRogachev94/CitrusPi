# 🍊 CitrusPi

Edge monitor for citrus grow tents. Runs on a Raspberry Pi Zero 2W.
Logs canopy temperature, humidity, and root-zone temperature every minute, snaps one daily timelapse photo, offers live camera preview, and serves a web dashboard.

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

# 1. Set the Pi timezone to your local time (photoperiod depends on local time)
sudo timedatectl set-timezone America/New_York  # Replace with your timezone

# 2. Run setup script
cd ~/citruspi && ./deploy/setup-pi.sh

# 3. Reboot the Pi (required for 1-Wire kernel overlay to activate)
sudo reboot
```
*Note: The script automatically enables I2C and 1-Wire, installs dependencies, secures `config.yaml`, and configures `log2ram` to protect your microSD card from write wear.*

Done. Open **http://citruspi.local** in your browser (no port needed).

---

## Development & Deployment Workflow (Git Push / Pull)

The recommended workflow is **writing code on your Mac and running everything live on the Raspberry Pi**:

1. **Hardware Parity:** The Pi physically hosts the I2C bus (`0x76` BME280), 1-Wire bus (DS18B20 probe on GPIO4 with 4.7kΩ pull-up to 3.3V), and USB camera (`/dev/video0`). Testing on the Pi guarantees real-world sensor communication and thermal/timing behavior.
2. **Zero Mac Clutter:** No need to install OpenCV, system packages, or manage Python 3.11 virtualenvs on your Mac.
3. **Seamless Deployment:** A single SSH command pulls your changes and restarts the systemd service.

### Quick Deploy Loop

1. Edit code on your Mac and push to your repo:
   ```bash
   git add -A
   git commit -m "Update feature"
   git push
   ```

2. Pull and restart on the Pi in one shot:
   ```bash
   ssh pi@citruspi.local "cd ~/citruspi && git pull && sudo systemctl restart citruspi"
   ```

3. (Optional) Stream live logs to verify:
   ```bash
   ssh pi@citruspi.local "sudo journalctl -u citruspi -f"
   ```

### Optional: Local Preview on Mac (Mock Mode)

If you ever want to test UI, styling, or API responses locally on your Mac without pushing to the Pi:
```bash
python -m citruspi --mock
```
*(Runs with simulated sensor readings and synthetic camera frames. If run as non-root on macOS, it automatically falls back from port 80 to port 8000).*


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
stream_width: 1280        # Live preview: 720p
stream_height: 720
capture_width: 1920       # Daily saved photos: Full HD (1080p)
capture_height: 1080
port: 80                  # Port 80 allows direct http://citruspi.local
```

Live preview and daily photos use separate resolutions. Daily capture opens the camera
at the configured photo resolution even while live view is running. Capture logs report
the actual saved dimensions and warn if the camera returns a different size.

The camera fullscreen button works with both live view and daily photos. It uses native
fullscreen when available and expands inside the page otherwise; Escape exits.
Temperature cards show the last 24 hours' minimum and maximum from stored readings.

AI health reports show a short headline and concise findings by visible area, with
visibility limits when foliage overlaps or the view is incomplete. Reports do not count
trees or assume hidden foliage is healthy. Status reflects current visible concerns:
healthy means continue monitoring, watch means check again, and alert means inspect.
Analysis compares today's photo with available photos from about 1 and 7 days earlier;
the report lists the actual reference dates. The first photo establishes a baseline,
so it cannot establish changes or new leaf drop. Previous AI reports are not used as
evidence. Optional `plant_layout` is set in YAML (not an environment variable); leave it
empty for camera-relative areas. Configured names are used only when clearly identifiable.
Older saved reports continue to display as plain text.

When ntfy notifications are configured, a daily photo still missing 30 minutes after
its scheduled time triggers one warning for that date. Capture retries continue;
failed notification deliveries are retried on subsequent scheduler checks. Late-night
schedules are checked after midnight when their grace period ends.

---

## Useful Commands

Local regression checks (after installing the `dev` dependencies):

```bash
python -m pytest
node --test tests/test_dashboard.cjs  # Optional UI logic checks; requires Node.js, no npm install
```

```bash
# View live logs
sudo journalctl -u citruspi -f

# Verify I2C sensors (0x76 = BME280)
sudo i2cdetect -y 1

# Verify 1-Wire DS18B20 probe
ls /sys/bus/w1/devices/

# Verify USB camera
v4l2-ctl --list-devices

# Check log2ram status
systemctl status log2ram
```
