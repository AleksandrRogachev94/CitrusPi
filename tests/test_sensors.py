"""Tests for mock sensor hub."""

from citruspi.config import Config
from citruspi.sensors import SensorHub


def test_mock_sensors_produce_readings() -> None:
    cfg = Config(mock_sensors=True)
    hub = SensorHub(cfg)
    assert hub.is_mock

    reading = hub.read()
    assert reading.temp_c is not None
    assert reading.temp_f is not None
    assert reading.humidity is not None
    assert reading.soil_moisture is not None
    assert 0 < reading.temp_c < 40
    assert 0 < reading.humidity <= 100

    d = reading.to_dict()
    assert "timestamp" in d
    assert "temp_c" in d


def test_mock_readings_vary() -> None:
    cfg = Config(mock_sensors=True)
    hub = SensorHub(cfg)
    r1 = hub.read()
    r2 = hub.read()
    # Random component means they should differ
    assert r1.temp_c != r2.temp_c or r1.humidity != r2.humidity
