# 🍊 CitrusPi

A clean, lightweight monitoring system for overwintering citrus trees in a grow tent.
Runs on a **Raspberry Pi Zero 2W** — tracks temperature, humidity, and soil
moisture every minute, captures a daily timelapse photo during the photoperiod,
provides an on-demand live camera stream, and serves a mobile-friendly dashboard.

## Highlights

- **1-Minute Sensor Resolution**: BME280 (temperature/humidity/pressure) and capacitive soil moisture logged to SQLite every 60 seconds (~115 KB/day).
- **Daily Timelapse Photo**: Snaps one photo per day at a configured hour during the light cycle (e.g. 22:00 / 10 PM), building a multi-month timelapse of leaf health.
- **On-Demand Live Stream**: Stream MJPEG video (~5 fps) directly in the browser when you want to look inside the tent. Shuts off when not viewing to keep the Pi CPU cool.
- **FastAPI + Jinja2 + HTMX**: Zero-build frontend that auto-refreshes every 30s.

## Quick Start on the Pi

Run the automated setup script on your Raspberry Pi Zero 2W:

```bash
# 1. Clone repository
git clone <repo-url> ~/citruspi && cd ~/citruspi

# 2. Run automated setup (enables I2C, installs packages, sets up systemd)
./deploy/setup-pi.sh
```

Open `http://<your-pi-ip>:8000` in your browser.

## Architecture

| Component | Hardware | Implementation |
|-----------|----------|----------------|
| **Air Temp & Humidity** | BME280 | I2C (address `0x76`), smbus2 driver |
| **Soil Moisture** | Capacitive Soil Sensor | I2C (address `0x36`) |
| **Camera** | Logitech USB Webcam | V4L2 (`/dev/video0`), OpenCV |
| **Storage** | 32GB microSD | SQLite (`data/citruspi.db`), JPEGs (`captures/`) |
| **Web Server** | Headless Linux | FastAPI + Uvicorn on port 8000 |

## Web Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Main dashboard (sensor cards, chart, live stream, latest photo) |
| GET | `/stream` | On-demand MJPEG camera stream (~5 fps) |
| GET | `/gallery` | Multi-day timelapse gallery with in-browser player |
| GET | `/api/current` | Latest sensor readings (JSON) |
| GET | `/api/history?hours=24` | Historical sensor data for graphing |
| POST | `/api/capture` | Trigger an immediate photo capture |
| POST | `/api/poll` | Trigger an immediate sensor reading |

## Configuration

Copy `config.example.yaml` → `config.yaml` to customize settings:

```yaml
poll_interval_sec: 60      # Sensor read frequency (seconds)
daily_capture_hour: 22     # Hour (0-23) for daily timelapse photo
daily_capture_minute: 0    # Minute (0-59)
stream_fps: 5              # Framerate for on-demand live stream
port: 8000                 # Web server port
```

## Pi Camera Stack Note

- Raspberry Pi OS Bullseye/Bookworm transitioned CSI ribbon-cable cameras to `libcamera` / `Picamera2`.
- **For this project's Logitech USB Webcam**, the standard Linux **UVC (USB Video Class) / V4L2** driver is used (`/dev/video0`). It is unaffected by the `libcamera` changes.
- To verify your webcam on the Pi: `v4l2-ctl --list-devices`.

## Why HTMX? (For React Developers)

HTMX is a ~14kB library that lets HTML elements issue AJAX requests directly via attributes (e.g. `hx-get="/fragments/readings" hx-trigger="every 30s" hx-swap="innerHTML"`). 

On an embedded board like the Pi Zero 2W (512MB RAM):
- **Zero build step**: No Node.js, Webpack, Vite, or TypeScript compilation on the Pi.
- **Near-zero memory**: Python renders HTML templates using Jinja2 with tiny RAM usage.
- If you prefer React, FastAPI already serves standard JSON APIs (`/api/current`, `/api/history`) with Swagger docs at `/docs`.

## Useful Commands

```bash
# Check service status
sudo systemctl status citruspi

# Follow live service logs
sudo journalctl -u citruspi -f

# Check I2C bus sensors
sudo i2cdetect -y 1

# Check USB video devices
v4l2-ctl --list-devices
```
