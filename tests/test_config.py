"""Tests for config loading and defaults."""

from pathlib import Path

import pytest
import yaml

from citruspi.config import Config, load_config


def test_default_config() -> None:
    cfg = load_config("/tmp/nonexistent.yaml")
    assert isinstance(cfg, Config)
    assert cfg.poll_interval_sec == 60
    assert cfg.daily_capture_hour == 22
    assert cfg.daily_capture_minute == 0
    assert cfg.port == 80
    assert cfg.w1_devices_dir == Path("/sys/bus/w1/devices")
    assert not hasattr(cfg, "soil_moisture_address")


def test_yaml_override(tmp_path: Path) -> None:
    f = tmp_path / "cfg.yaml"
    f.write_text(
        yaml.dump(
            {
                "poll_interval_sec": 30,
                "port": 9000,
                "mock_sensors": True,
                "w1_devices_dir": str(tmp_path / "w1"),
            }
        )
    )
    cfg = load_config(str(f))
    assert cfg.poll_interval_sec == 30
    assert cfg.port == 9000
    assert cfg.mock_sensors is True
    assert cfg.w1_devices_dir == tmp_path / "w1"
    # defaults preserved
    assert cfg.daily_capture_hour == 22
