"""Tests for config loading and defaults."""

from pathlib import Path

import pytest
import yaml

from citruspi.config import Config, load_config


def test_default_config() -> None:
    cfg = load_config("/tmp/nonexistent.yaml")
    assert isinstance(cfg, Config)
    assert cfg.poll_interval_sec == 300
    assert cfg.lights_on_hour == 7
    assert cfg.lights_off_hour == 17
    assert cfg.port == 8000


def test_yaml_override(tmp_path: Path) -> None:
    f = tmp_path / "cfg.yaml"
    f.write_text(yaml.dump({"poll_interval_sec": 60, "port": 9000, "mock_sensors": True}))
    cfg = load_config(str(f))
    assert cfg.poll_interval_sec == 60
    assert cfg.port == 9000
    assert cfg.mock_sensors is True
    # defaults preserved
    assert cfg.lights_on_hour == 7
