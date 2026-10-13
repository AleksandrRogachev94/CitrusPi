# 🛠️ Raspberry Pi Zero 2W Bootstrap Guide

Complete step-by-step instructions for bringing up a brand-new Raspberry Pi Zero 2W
running **Raspberry Pi OS Lite (64-bit)** from bare metal to a running CitrusPi node.

---

## Step 1: Flashing the SD Card (from your computer)

1. Download and open **Raspberry Pi Imager** ([raspberrypi.com/software](https://www.raspberrypi.com/software/)).
2. **Device**: Select `Raspberry Pi Zero 2 W`.
3. **Operating System**: Select `Raspberry Pi OS (other)` → **`Raspberry Pi OS Lite (64-bit)`**.
4. **Storage**: Select your microSD card.
5. When prompted to apply OS customization settings (the gear icon):
   - **Hostname**: `citruspi` (makes it reachable at `citruspi.local`)
   - **Set username and password**: e.g., `pi` and your password
   - **Configure wireless LAN**: Enter your 2.4GHz Wi-Fi SSID and password
   - **Services**: Check **Enable SSH** (use password authentication or paste your public key)
6. Click **Write** and wait for verification to complete.

---

## Step 2: First Boot & Connecting

1. Insert the microSD card into the Pi Zero 2W.
2. Connect standard 5V Micro-USB power to the **PWR IN** port (outer port).
3. Connect your Logitech webcam to the **USB OTG** port (inner port) via an OTG adapter.
4. Wait 60–90 seconds for initial boot and Wi-Fi association.
5. From your Mac terminal, SSH into the Pi:

```bash
ssh pi@citruspi.local
```

*(If `citruspi.local` does not resolve, check your home router for the IP address and run `ssh pi@<ip-address>`)*.

---

## Step 3: Install Git & Transfer Code

Fresh Raspberry Pi OS Lite does **not** have `git` pre-installed. Update packages and install git:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git
```

### Getting the code onto the Pi:

**Option A — If your code is on GitHub / GitLab:**
```bash
git clone <your-git-repo-url> ~/citruspi
cd ~/citruspi
```

**Option B — Directly copy from your Mac without pushing to git:**
Run this command from your Mac terminal:
```bash
rsync -avz --exclude '.venv' --exclude 'data' --exclude 'captures' \
  ~/Documents/code/citruspi/ pi@citruspi.local:~/citruspi/
```
Then switch back to your SSH session on the Pi:
```bash
cd ~/citruspi
```

---

## Step 4: Run the Automated Setup Script

Run the automated installer on the Pi:

```bash
chmod +x deploy/setup-pi.sh
./deploy/setup-pi.sh
```

### What this script does automatically:
1. **Enables the I2C interface** (`raspi-config nonint do_i2c 0`).
2. **Installs system packages**: `python3-venv`, `python3-pip`, `i2c-tools`, `v4l-utils`.
3. **Installs pre-compiled OpenCV (`python3-opencv`)**: Prevents pip from trying to compile OpenCV from source on the Pi Zero 2W's 512MB RAM (which would trigger out-of-memory freeze).
4. **Builds the virtualenv**: Uses `--system-site-packages` so Python inherits system OpenCV while isolating pip packages.
5. **Registers & starts systemd service**: CitrusPi runs in the background on startup.

---

## Step 5: Hardware Verification

Verify all hardware devices are recognized on the Pi:

### 1. Check I2C Sensors (BME280 & Soil Moisture)
```bash
sudo i2cdetect -y 1
```
Expected output:
- `0x76` (or `0x77`): Bosch BME280 air microclimate sensor
- `0x36`: Adafruit STEMMA / Seesaw capacitive soil sensor

### 2. Check USB Webcam
```bash
v4l2-ctl --list-devices
```
Expected output should list your Logitech USB Webcam (e.g., `/dev/video0` and `/dev/video1`).

### 3. Check CitrusPi Service Status & Logs
```bash
sudo systemctl status citruspi
sudo journalctl -u citruspi -f
```

---

## Step 6: Storage Preservation with `log2ram` (Recommended)

To protect your microSD card flash cells from constant syslog writes over several months of overwintering, install `log2ram` to redirect `/var/log` to RAM:

```bash
echo "deb [signed-by=/usr/share/keyrings/azlux-archive-keyring.gpg] http://packages.azlux.fr/debian/ bookworm main" | sudo tee /etc/apt/sources.list.d/azlux.list
sudo wget -O /usr/share/keyrings/azlux-archive-keyring.gpg https://azlux.fr/repo.gpg
sudo apt update && sudo apt install -y log2ram
sudo reboot
```

After reboot, verify it is active:
```bash
systemctl status log2ram
```

---

## Accessing Your Dashboard

Open a browser on your phone or computer on the same Wi-Fi network:

```
http://citruspi.local:8000
```
*(or `http://<pi-ip-address>:8000`)*
