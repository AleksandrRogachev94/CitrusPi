"""Tests for sensor hub, DS18B20 1-Wire probe, and strict mock mode."""

from pathlib import Path

from citruspi.config import Config
from citruspi.sensors import SensorHub, _DS18B20


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
    assert probe.read_temp_c() == 21.5


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

