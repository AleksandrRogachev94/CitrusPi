# 🍊 CitrusPi

A clean, robust monitoring system for overwintering citrus trees in a sealed grow tent.
Built for the **Raspberry Pi Zero 2W**, this node logs microclimate data every minute, captures a daily daylight photo for long-term timelapse review, streams live video on demand, and serves a mobile-friendly dashboard.

---

## 🚀 Getting Started on a Fresh Pi Zero 2W

If you just booted a fresh **Raspberry Pi OS Lite (64-bit)** image, `git` is **not** installed yet!

### 1. Connect via SSH & Install Git
```bash
ssh pi@citruspi.local

# Update package lists and install git
sudo apt update && sudo apt upgrade -y
sudo apt install -y git
```

### 2. Get the Code
* **Option A (from Git):**
  ```bash
  git clone <your-repo-url> ~/citruspi && cd ~/citruspi
  ```
* **Option B (Copy directly from your Mac terminal without pushing):**
  ```bash
  # Run this on your Mac:
  rsync -avz --exclude '.venv' --exclude 'data' --exclude 'captures' \
    ~/Documents/code/citruspi/ pi@citruspi.local:~/citruspi/
  ```

### 3. Run the Automated Setup
```bash
cd ~/citruspi
./deploy/setup-pi.sh
```

The script automatically:
* Enables hardware I2C interface.
* Installs `python3-venv`, `i2c-tools`, `v4l-utils`, and pre-compiled ARM OpenCV (`python3-opencv`).
* Builds the virtual environment inheriting system packages (saving the 512MB RAM Pi Zero from compilation freeze).
* Registers and starts the `citruspi` background `systemd` service.

👉 **For complete first-boot instructions & SD card flashing settings**, see the [Bootstrap Guide](file:///Users/alex/Documents/code/citruspi/deploy/BOOTSTRAP.md).

---

## 🏛️ Database Architecture & Management

Instead of heavy ORMs (like SQLAlchemy + Alembic) that add tens of megabytes of memory pressure to a 512MB RAM embedded board, CitrusPi uses an **engineered SQLite architecture with automated migrations**:

### Concurrency & Write Safety
* **WAL Mode (`PRAGMA journal_mode = WAL`)**: Allows concurrent background sensor writes and web read queries without database lock contention.
* **`PRAGMA synchronous = NORMAL`**: Drastically reduces microSD flash wear while remaining fully crash-safe in WAL mode.
* **`PRAGMA busy_timeout = 5000`**: Thread-safe retry logic prevents lock exceptions.

### Indexing Strategy
* **Primary Key (`id`)**: Auto-indexed for fast retrieval of the latest sample (`ORDER BY id DESC LIMIT 1`).
* **B-Tree Index on Timestamp (`idx_readings_timestamp`)**:
  ```sql
  CREATE INDEX IF NOT EXISTS idx_readings_timestamp ON readings (timestamp);
  ```
  All history queries (`WHERE timestamp >= ...`) use ISO8601 UTC bounds (`YYYY-MM-DDTHH:MM:SSZ`), allowing direct B-tree index seek rather than expensive full-table scans.

### Automated Schema Migrations
Database changes are versioned in `src/citruspi/store.py` and tracked in `schema_migrations`:
```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
```
On boot, pending migrations are applied automatically inside an ACID transaction. New columns (e.g., `root_temp_c`, `light_lux`) are added forward-compatibly without manual SQL commands.

---

## 🛠️ Senior Engineering Standards

CitrusPi follows modern Python and systems best practices:

* **PEP 517 / 518 / 621 Project Standard**: Modern `pyproject.toml` with `hatchling` backend and explicit optional dependencies (`[project.optional-dependencies]`).
* **`src/` Layout**: Prevents accidental import of local uninstalled directories during testing and avoids path collisions.
* **Typed Domain Models**: Sensor payloads use immutable, validated dataclass models ([Reading](file:///Users/alex/Documents/code/citruspi/src/citruspi/models.py)) with auto unit conversion (°C ⇄ °F) and serialization.
* **Thread Safety**: Camera access and database connections use isolated thread-local storage and thread synchronization mutexes (`threading.Lock`).
* **MicroSD Preservation**: Syslogs can be offloaded to memory via `log2ram`, and daily telemetry writes are indexed and buffered.

---

## 📱 Features & Endpoints

| Endpoint | Method | Role |
|----------|--------|------|
| `/` | `GET` | Live dashboard (htmx auto-refresh, Chart.js 24h trends, stream toggle) |
| `/stream` | `GET` | On-demand MJPEG camera stream (~5 fps, releases camera when closed) |
| `/gallery` | `GET` | Multi-day timelapse viewer with variable playback speed |
| `/api/current` | `GET` | Latest telemetry reading in JSON |
| `/api/history` | `GET` | Historical data points for custom graphing |
| `/api/capture` | `POST` | Manual snapshot trigger |
| `/api/poll` | `POST` | Immediate sensor poll trigger |
| `/docs` | `GET` | Interactive OpenAPI / Swagger UI |

---

## ⚙️ Configuration

Copy `config.example.yaml` → `config.yaml` to customize:

```yaml
poll_interval_sec: 60      # Telemetry cadence (seconds)
daily_capture_hour: 22     # 22:00 (10 PM) night photoperiod snapshot
daily_capture_minute: 0
stream_fps: 5              # Framerate for live preview
port: 8000                 # Web server port
```

---

## 🔍 Hardware Diagnostics

Once deployed on the Pi:
```bash
# Verify I2C sensors (0x76 = BME280, 0x36 = Soil moisture)
sudo i2cdetect -y 1

# Verify USB webcam
v4l2-ctl --list-devices

# View live application logs
sudo journalctl -u citruspi -f
```

## 📄 License
MIT
