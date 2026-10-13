# 🍊 CitrusPi

A simple monitoring dashboard for overwintering citrus trees in a grow tent.
Runs on a **Raspberry Pi Zero 2W** — tracks temperature, humidity, and soil
moisture, snaps periodic photos for timelapse review, and serves a live
web dashboard you can check from your phone.

## Quick Start

```bash
git clone <repo> ~/citruspi && cd ~/citruspi
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Start with mock sensors (for development on any machine)
python -m citruspi --mock

# Open http://localhost:8000
```

## What It Does

| Feature | How |
|---------|-----|
| **Temperature & Humidity** | BME280 over I2C, polled every 5 min |
| **Soil Moisture** | Capacitive sensor over I2C |
| **Camera** | USB webcam, photos every 30 min during daylight |
| **Storage** | SQLite for sensor data, dated folders for photos |
| **Dashboard** | FastAPI + htmx, auto-refreshing, mobile-friendly |
| **Timelapse** | Gallery page with built-in timelapse player |

## Project Layout

```
citruspi/
├── src/citruspi/
│   ├── __main__.py     # Entry point
│   ├── config.py       # YAML-backed configuration
│   ├── sensors.py      # BME280 + soil moisture (with mock fallback)
│   ├── camera.py       # Daylight-aware photo capture
│   ├── store.py        # SQLite telemetry storage
│   ├── web.py          # FastAPI dashboard & API
│   ├── templates/      # Jinja2 HTML templates
│   └── static/         # CSS
├── tests/              # pytest suite
├── deploy/             # systemd unit file
├── captures/           # Photos (gitignored, created at runtime)
├── data/               # SQLite DB (gitignored, created at runtime)
├── config.example.yaml # Reference configuration
└── pyproject.toml
```

## Configuration

Copy `config.example.yaml` → `config.yaml` and edit as needed.
All settings have sensible defaults — it works out of the box.

## Deploying to the Pi

```bash
# On the Pi
git clone <repo> ~/citruspi && cd ~/citruspi
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Install the systemd service
sudo cp deploy/citruspi.service /etc/systemd/system/
sudo systemctl enable --now citruspi

# Check it
sudo journalctl -u citruspi -f
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard |
| GET | `/gallery` | Photo gallery + timelapse |
| GET | `/api/current` | Latest sensor reading |
| GET | `/api/history?hours=24` | Historical data (JSON) |
| GET | `/api/photos?date=YYYY-MM-DD` | Photo list for a date |
| POST | `/api/capture` | Trigger a manual photo |
| POST | `/api/poll` | Trigger a manual sensor read |

## Development

```bash
pytest                    # run tests
ruff check src/ tests/    # lint
ruff format src/ tests/   # format
```

## License

MIT
