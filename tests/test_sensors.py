"""Tests for sensor hub, DS18B20 1-Wire probe, and strict mock mode."""

import struct
from pathlib import Path

import pytest

from citruspi.config import Config
from citruspi.sensors import _DS18B20, SensorHub

# ── Mock Sensors Tests ───────────────────────────────────────────────────────


def test_mock_sensors_produce_readings() -> None:
    cfg = Config(mock_sensors=True)
    hub = SensorHub(cfg)
    assert hub.is_mock

    reading = hub.read()
    assert reading.temp_c is not None
    assert reading.temp_f is not None
    assert reading.humidity is not None
    assert reading.root_temp_c is not None
    assert reading.root_temp_f is not None
    assert reading.soil_moisture is None
    assert 0 < reading.temp_c < 40
    assert 0 < reading.root_temp_c < 40
    assert 0 < reading.humidity <= 100

    d = reading.to_dict()
    assert "timestamp" in d
    assert "temp_c" in d
    assert "root_temp_c" in d
    assert "root_temp_f" in d


def test_mock_readings_vary() -> None:
    cfg = Config(mock_sensors=True)
    hub = SensorHub(cfg)
    r1 = hub.read()
    r2 = hub.read()
    # Random variation means readings should differ
    assert r1.temp_c != r2.temp_c or r1.root_temp_c != r2.root_temp_c or r1.humidity != r2.humidity


# ── DS18B20 1-Wire Driver Parsing Tests ──────────────────────────────────────


def test_ds18b20_valid_reading(tmp_path: Path) -> None:
    dev_dir = tmp_path / "28-000001a2b3c4"
    dev_dir.mkdir(parents=True, exist_ok=True)
    slave_file = dev_dir / "w1_slave"
    slave_file.write_text(
        "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n"
        "72 01 4b 46 7f ff 0e 10 57 t=21500\n"
    )

    probe = _DS18B20(tmp_path)
    val = probe.read()
    assert val == 21.5


def test_ds18b20_negative_temperature(tmp_path: Path) -> None:
    dev_dir = tmp_path / "28-000001a2b3c4"
    dev_dir.mkdir(parents=True, exist_ok=True)
    slave_file = dev_dir / "w1_slave"
    slave_file.write_text(
        "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n"
        "72 01 4b 46 7f ff 0e 10 57 t=-5500\n"
    )

    probe = _DS18B20(tmp_path)
    assert probe.read() == -5.5


def test_ds18b20_crc_fail(tmp_path: Path) -> None:
    dev_dir = tmp_path / "28-000001a2b3c4"
    dev_dir.mkdir(parents=True, exist_ok=True)
    slave_file = dev_dir / "w1_slave"
    # CRC failed: line 1 ends with NO
    slave_file.write_text(
        "72 01 4b 46 7f ff 0e 10 57 : crc=57 NO\n"
        "72 01 4b 46 7f ff 0e 10 57 t=21500\n"
    )

    probe = _DS18B20(tmp_path)
    assert probe.read() is None


def test_ds18b20_85000_sentinel(tmp_path: Path) -> None:
    dev_dir = tmp_path / "28-000001a2b3c4"
    dev_dir.mkdir(parents=True, exist_ok=True)
    slave_file = dev_dir / "w1_slave"
    # 85000 is DS18B20 power-on reset sentinel before first conversion
    slave_file.write_text(
        "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n"
        "72 01 4b 46 7f ff 0e 10 57 t=85000\n"
    )

    probe = _DS18B20(tmp_path)
    assert probe.read() is None


def test_ds18b20_missing_device(tmp_path: Path) -> None:
    # Empty directory (no 28-* devices)
    probe = _DS18B20(tmp_path)
    assert probe.read() is None

    # Non-existent directory
    probe_nonexistent = _DS18B20(tmp_path / "nonexistent")
    assert probe_nonexistent.read() is None


def test_ds18b20_rescan_and_cache_invalidation(tmp_path: Path) -> None:
    probe = _DS18B20(tmp_path)
    # Rescan on every read if not found
    assert probe.read() is None

    # Device plugged in
    dev_dir = tmp_path / "28-000002"
    dev_dir.mkdir(parents=True, exist_ok=True)
    slave_file = dev_dir / "w1_slave"
    slave_file.write_text(
        "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n"
        "72 01 4b 46 7f ff 0e 10 57 t=18250\n"
    )
    assert probe.read() == 18.25
    assert probe._device_file == slave_file

    # Parse failure forgets cached path
    slave_file.write_text("malformed content\nno temperature\n")
    assert probe.read() is None
    assert probe._device_file is None

    # OSError / file removed forgets cached path
    slave_file.unlink()
    assert probe.read() is None
    assert probe._device_file is None


# ── Strict Mock Rule Tests ───────────────────────────────────────────────────


def test_sensorhub_real_mode_never_returns_mock_data(tmp_path: Path) -> None:
    """When config.mock_sensors is False, real mode must NEVER fall back to mock data."""
    cfg = Config(mock_sensors=False, w1_devices_dir=tmp_path)
    hub = SensorHub(cfg)

    # Must not be marked as mock
    assert not hub.is_mock

    # When physical hardware is absent, failed reads return None fields
    reading = hub.read()
    assert reading.temp_c is None
    assert reading.temp_f is None
    assert reading.humidity is None
    assert reading.pressure is None
    assert reading.root_temp_c is None
    assert reading.root_temp_f is None
    assert reading.soil_moisture is None
    assert reading.vpd_kpa is None


def test_sensorhub_real_mode_reads_w1_probe(tmp_path: Path) -> None:
    """When a real 1-Wire probe is present, root_temp_c is populated and temp_f calculated."""
    dev_dir = tmp_path / "28-000001a2b3c4"
    dev_dir.mkdir(parents=True, exist_ok=True)
    slave_file = dev_dir / "w1_slave"
    slave_file.write_text(
        "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n"
        "72 01 4b 46 7f ff 0e 10 57 t=16500\n"
    )

    cfg = Config(mock_sensors=False, w1_devices_dir=tmp_path)
    hub = SensorHub(cfg)
    assert not hub.is_mock

    reading = hub.read()
    assert reading.root_temp_c == 16.5
    assert reading.root_temp_f == 61.7
    # I2C BME280 remains None without throwing or falling back to mock
    assert reading.temp_c is None


def test_sensorhub_bme_lazy_reinit(tmp_path: Path) -> None:
    """If BME280 fails at boot, read() retries after cooldown."""
    cfg = Config(mock_sensors=False, w1_devices_dir=tmp_path)
    hub = SensorHub(cfg)
    assert hub._bme is None

    # Artificially expire cooldown
    hub._last_bme_init_attempt = 0.0

    # Mock BME driver on hub to test recovery
    class FakeBME:
        def read(self) -> dict[str, float]:
            return {"temp_c": 19.5, "humidity": 50.0, "pressure": 1012.0}

    # Simulate lazy recovery succeeding
    hub._bme = FakeBME()  # type: ignore[assignment]
    reading = hub.read()
    assert reading.temp_c == 19.5
    assert reading.humidity == 50.0


def test_bme280_calibration_sign_extension() -> None:
    from unittest.mock import MagicMock

    from citruspi.sensors import _BME280

    mock_bus = MagicMock()

    def mock_rb(addr: int, reg: int, n: int) -> list[int]:
        calib_data = {
            0xD0: [0x60],
            0x88: [0] * 26,  # CALIB_T_P
            0xA1: [75],      # DIG_H1
            0xE1: [0, 0, 0, 0xF0, 0x35, 0x80, 0],  # CALIB_H
        }
        return calib_data.get(reg, [0] * n)

    mock_bus.read_i2c_block_data.side_effect = mock_rb
    bme = _BME280(mock_bus, 0x76)
    assert bme._cal["H4"] == -251
    assert bme._cal["H5"] == -2045


class _ConversionBus:
    """Emulate reset values remaining visible until a conversion finishes."""

    def __init__(self) -> None:
        self.chip_id = 0x60
        self.statuses: list[int] = []
        self.writes: list[tuple[int, int]] = []
        self.events: list[tuple[str, int]] = []
        self.pending = False
        self.stuck_conversion = False
        self.raw_temperature = 519888
        self.raw_pressure = 415148
        self.raw_humidity = 35000
        self.data = [0x80, 0, 0, 0x80, 0, 0, 0x80, 0]

    def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        self.writes.append((reg, value))
        if reg == 0xE0:
            self.statuses = [1, 1, 0]
        elif reg == 0xF4 and value & 0x03:
            self.pending = True
            self.statuses = [0x09, 0x08, 0]

    def read_i2c_block_data(self, addr: int, reg: int, length: int) -> list[int]:
        self.events.append(("read", reg))
        if reg == 0xD0:
            return [self.chip_id]
        if reg == 0xF3:
            if self.pending and self.stuck_conversion:
                return [0x08]
            status = self.statuses.pop(0) if self.statuses else 0
            if status == 0 and self.pending:
                self.pending = False
                p, t, h = self.raw_pressure, self.raw_temperature, self.raw_humidity
                self.data = [
                    p >> 12, (p >> 4) & 0xFF, (p & 0x0F) << 4,
                    t >> 12, (t >> 4) & 0xFF, (t & 0x0F) << 4,
                    h >> 8, h & 0xFF,
                ]
            return [status]
        if reg in (0x88, 0xA1, 0xE1):
            assert not self.statuses, "Calibration read before NVM copy completed"
            # Bosch temperature/pressure example coefficients.
            calibration = {
                0x88: list(struct.pack(
                    "<HhhHhhhhhhhhBB", 27504, 26435, -1000, 36477,
                    -10685, 3024, 2855, 140, -7, 15500, -14600, 6000, 0, 75,
                )),
                0xA1: [75],
                0xE1: [0x6A, 0x01, 0, 0x14, 0x25, 0x03, 30],
            }
            return calibration[reg]
        if reg == 0xF7:
            return self.data
        raise AssertionError(f"Unexpected register 0x{reg:02X}")


@pytest.fixture
def conversion_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]

    def advance(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr("citruspi.sensors.time.sleep", advance)
    monkeypatch.setattr("citruspi.sensors.time.monotonic", lambda: now[0])


def test_bme280_first_read_waits_for_fresh_conversion(conversion_clock: None) -> None:
    from citruspi.sensors import _BME280

    bus = _ConversionBus()
    bme = _BME280(bus, 0x76)
    # Construction must not leave a conversion running into the first read.
    assert not bus.pending
    assert (0xF4, 0x25) not in bus.writes
    assert (0xF4, 0x24) in bus.writes

    first = bme.read()
    assert first["temp_c"] == 25.08
    assert first["pressure"] == 1006.53
    assert 0 < first["humidity"] < 100
    assert not bus.pending

    bus.raw_temperature = 500000
    second = bme.read()
    assert second["temp_c"] != first["temp_c"]
    assert bus.writes.count((0xF4, 0x25)) == 2


def test_bme280_conversion_timeout_does_not_read_stale_data(conversion_clock: None) -> None:
    from citruspi.sensors import _BME280

    bus = _ConversionBus()
    bme = _BME280(bus, 0x76)
    bus.stuck_conversion = True
    with pytest.raises(TimeoutError, match="did not finish"):
        bme.read()
    assert ("read", 0xF7) not in bus.events


@pytest.mark.parametrize("channel", ["temperature", "pressure", "humidity"])
def test_bme280_rejects_reset_measurements(channel: str, conversion_clock: None) -> None:
    from citruspi.sensors import _BME280

    bus = _ConversionBus()
    setattr(bus, f"raw_{channel}", 0x8000 if channel == "humidity" else 0x80000)
    bme = _BME280(bus, 0x76)
    with pytest.raises(ValueError, match="reset/skipped measurement"):
        bme.read()


def test_bme280_rejects_bmp280_before_configuration(conversion_clock: None) -> None:
    from citruspi.sensors import _BME280

    bus = _ConversionBus()
    bus.chip_id = 0x58
    with pytest.raises(ValueError, match="Expected BME280 chip ID"):
        _BME280(bus, 0x76)
    assert bus.writes == []


def test_sensorhub_bme_read_error_closes_bus(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    cfg = Config(mock_sensors=False, w1_devices_dir=tmp_path)
    hub = SensorHub(cfg)

    mock_bus = MagicMock()
    mock_bme = MagicMock()
    mock_bme.read.side_effect = OSError("I2C read failure")

    hub._bus = mock_bus
    hub._bme = mock_bme

    reading = hub.read()
    assert reading.temp_c is None
    assert hub._bme is None
    assert hub._bus is None
    mock_bus.close.assert_called_once()



