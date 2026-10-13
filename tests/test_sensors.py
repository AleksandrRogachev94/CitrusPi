"""Tests for mock sensor hub."""

from citruspi.config import Config
from citruspi.sensors import SensorHub


def test_mock_sensors_produce_readings() -> None:
    cfg = Config(mock_sensors=True)
    hub = SensorHub(cfg)
    assert hub.is_mock

    reading = hub.read()
    assert "temp_c" in reading
    assert "temp_f" in reading
    assert "humidity" in reading
    assert "soil_moisture" in reading
    assert 0 < reading["temp_c"] < 40
    assert 0 < reading["humidity"] <= 100


def test_mock_readings_vary() -> None:
    cfg = Config(mock_sensors=True)
    hub = SensorHub(cfg)
    r1 = hub.read()
    r2 = hub.read()
    # Random component means they should differ
    assert r1 != r2
