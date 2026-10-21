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

## Optional Kasa Plug Monitoring

Install the optional dependency on the Pi (also included in `dev` dependencies):

```bash
cd ~/citruspi
.venv/bin/pip install -e ".[plugs]"
```

Add plugs to `config.yaml`; the commented examples in `config.example.yaml` use the
heater at `192.168.1.230` and grow light at `192.168.1.229`. Reserve these IPs in the
router. Each `id` identifies equipment history, so keep it stable when renaming a plug.
If a plug is reassigned to different equipment, give it a new id.

Set `kasa_username` and `kasa_password` in the ignored `config.yaml`, then run
`chmod 600 config.yaml`. These are your TP-Link account credentials, used for local
authentication; readings are requested directly from each plug, not from a cloud API.
`CITRUSPI_KASA_USERNAME` and `CITRUSPI_KASA_PASSWORD` override these settings if you
already manage secrets through the service environment. Shell exports alone do not
change the environment of the systemd service. Credentials are never included in
dashboard/API responses or diagnostic JSON.

Verify access **before restarting the service**:

```bash
.venv/bin/python -m citruspi --check-plugs
```

This reads each configured plug once, prints watts, activity, relay state and available
device metadata, and exits. It does not write telemetry or switch/configure devices.
Exit status is nonzero if any plug cannot provide power readings. If authentication
fails, check credentials and the Kasa app's **Settings > Third-Party Compatibility**.
Supported models do not guarantee support for every firmware version; unsupported
firmware is reported explicitly. The diagnostic uses real devices even with `--mock`.

Once both report `"status": "ok"`, restart CitrusPi:

```bash
sudo systemctl restart citruspi
```

Monitoring polls every 60 seconds in its own thread and reconnects after errors, so
plug failures do not block temperature monitoring. With no plugs configured, the
dashboard is unchanged and the Kasa dependency is not required. Normal `--mock` mode
pauses all plug polling and labels the equipment section accordingly.

Equipment cards distinguish **plug enabled** from **equipment active**, inferred
from measured watts. Tune `active_watts` and `idle_watts` to the observed heater/light
loads. Initial activity is detected immediately outside the threshold band; subsequent
changes require two consecutive observations, with hysteresis between the thresholds.
Missing power resets activity to unknown. Failures and stale readings never count as off.

The main history view aligns heater/light activity lanes with canopy/root temperature
over a shared 1h–30d range. Colored segments mean active, gray means observed idle,
and gaps mean unknown. A single observation is shown as a marker, not an invented
duration. Select or focus a period to see its start, end and estimated duration.
Lane totals follow the selected range; expandable active-period lists show the latest
20 observed periods per device. Gaps are excluded and boundary periods are clipped
to the selected range. Activity remains visible without temperature readings. AI findings stay
collapsed beneath their summary. Equipment cards show current state, watts and the
plug's daily energy counter; expandable details contain watts history and usage estimates.
Readings persist in SQLite alongside existing sensor history. Rolling 24h runtime,
duty cycle and energy are estimates from raw observations; intervals with missing
endpoints or gaps longer than twice the polling period are excluded. Coverage makes
partial collection visible, and no usage is extrapolated after the last sample.
Runtime uses the preceding observed state; energy uses the average endpoint watts.
The plug's own **today counter** is shown separately: it uses the device's day boundary,
can reset, and is never summed into our rolling estimate. Detailed history starts when
CitrusPi begins collecting. This integration only observes; it adds no switching or
automatic equipment alerts.

The dashboard's **History range** applies to all charts, equipment runtime/energy,
and estimated electricity costs. Live readings and the plug's **Today** counter
remain current. Choose 1h–30d; the selection is remembered in your browser.

To estimate electricity cost, add the `electricity` section from
`config.example.yaml` to `config.yaml` and restart CitrusPi. The example includes
PECO residential TOU supply + transmission rates for June 1–November 30, 2026:
`peak_rate: 0.32404`, `off_peak_rate: 0.09336`, and
`super_off_peak_rate: 0.06741`, in **dollars per kWh**. See
[PECO's published rate table](https://azure-na-assets.contentstack.com/v3/assets/blt1b5616c79bacadb4/bltf7c1eb06cdfe6ab9/6a0e7050df378b4731251e82/2026_RESIDENTIAL_Final_Electric_PTC_Effective_06-01-2026.pdf).
Peak defaults to weekdays 14:00–18:00, overnight to 00:00–06:00 every day,
and off-peak to all remaining hours, using `America/New_York` (including DST).
Hours are start-inclusive/end-exclusive; windows must stay within one day and
must not overlap. `peak_weekdays` uses Monday=0 through Sunday=6. Listed
`holidays` use off-peak instead of peak; maintain those dates as needed.

The equipment section shows a combined cost for monitored plugs in the selected
range, with each plug's cost and data coverage in the details. Missing intervals
are excluded, so partial coverage produces a partial estimate, never a projection.
Rates are applied across interval boundaries using the same interpolated watts
as the energy estimate. All history uses the **current configured rates**; changing
rates recalculates the estimate. No billing records or rate history are maintained.
The example also includes `delivery_rate: 0.10236` USD/kWh, PECO's Rate R
variable distribution charge effective July 1, 2026, from
[PECO's residential tariff](https://azure-na-assets.contentstack.com/v3/assets/blt1b5616c79bacadb4/bltef2bb157cc587eac/6a4541d70d15ec68610f09eb/Rate_R_from_clean_up_FARS_ICUS_Supp_no._32_Effective_July_1_2026.pdf).
This adds the same delivery charge to each kWh, giving supply + transmission +
variable delivery estimates. Set it to `0` for supply only. Fixed monthly fees
and other bill adjustments are excluded. Verify rates against your bill and update them when PECO changes
them. Remove `electricity` or set it to `null` to hide cost estimates.

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

AI health reports show a short summary and notable findings by physical location.
Reports describe visible foliage without counting trees or assessing hidden leaves.
Status reflects current visible concerns:
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
node --test tests/test_plugs.cjs      # Equipment cards and power history behavior
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
