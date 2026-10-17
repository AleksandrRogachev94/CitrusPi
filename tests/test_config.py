"""Tests for config loading and defaults."""

from pathlib import Path

import pytest
import yaml

from citruspi.config import Config, load_config


def test_default_config() -> None:
    cfg = Config()
    assert isinstance(cfg, Config)
    assert cfg.poll_interval_sec == 60
    assert cfg.daily_capture_hour == 22
    assert cfg.daily_capture_minute == 0
    assert cfg.port == 80
    assert cfg.w1_devices_dir == Path("/sys/bus/w1/devices")
    assert not hasattr(cfg, "soil_moisture_address")
    assert cfg.ntfy_url == "https://ntfy.sh"
    assert cfg.ntfy_topic == ""
    assert cfg.heartbeat_url == ""
    assert cfg.temp_min_crit_f == 38.0
    assert cfg.temp_min_warn_f == 44.0
    assert cfg.temp_max_crit_f == 85.0
    assert cfg.alert_debounce_count == 3
    assert cfg.ai_enabled is False
    assert cfg.ai_api_key == ""
    assert cfg.ai_model == "gemini-3.8-flash"
    assert cfg.plant_layout == ""
    assert cfg.capture_width == 1920
    assert cfg.capture_height == 1080
    assert cfg.stream_width == 1280
    assert cfg.stream_height == 720
    assert cfg.stream_max_session_sec == 300


def test_missing_config_raises_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/citruspi_config.yaml")


def test_yaml_override(tmp_path: Path) -> None:
    f = tmp_path / "cfg.yaml"
    f.write_text(
        yaml.dump(
            {
                "poll_interval_sec": 30,
                "port": 9000,
                "mock_sensors": True,
                "w1_devices_dir": str(tmp_path / "w1"),
                "ntfy_topic": "yaml-alerts",
                "temp_min_crit_f": 35.0,
                "alert_debounce_count": 5,
                "ai_enabled": True,
                "ai_api_key": "yaml-api-key",
                "ai_model": "gemini-pro-custom",
                "plant_layout": "Front: Kishu; Back: Meyer",
            }
        )
    )
    cfg = load_config(str(f))
    assert cfg.poll_interval_sec == 30
    assert cfg.port == 9000
    assert cfg.mock_sensors is True
    assert cfg.w1_devices_dir == tmp_path / "w1"
    assert cfg.ntfy_topic == "yaml-alerts"
    assert cfg.temp_min_crit_f == 35.0
    assert cfg.alert_debounce_count == 5
    assert cfg.ai_enabled is True
    assert cfg.ai_api_key == "yaml-api-key"
    assert cfg.ai_model == "gemini-pro-custom"
    assert cfg.plant_layout == "Front: Kishu; Back: Meyer"
    # defaults preserved
    assert cfg.daily_capture_hour == 22


def test_alerting_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CITRUSPI_NTFY_TOPIC", "my-lemon-tent")
    monkeypatch.setenv("CITRUSPI_HEARTBEAT_URL", "https://heartbeat.example.com/snitch")
    monkeypatch.setenv("CITRUSPI_AI_API_KEY", "env-api-key-xyz")
    empty_cfg = tmp_path / "empty.yaml"
    empty_cfg.write_text("{}")
    cfg = load_config(str(empty_cfg))
    assert cfg.ntfy_topic == "my-lemon-tent"
    assert cfg.heartbeat_url == "https://heartbeat.example.com/snitch"
    assert cfg.ai_api_key == "env-api-key-xyz"


def test_camera_device_and_index_synchronization() -> None:
    # Setting index should update camera_device
    c1 = Config(camera_device_index=2)
    assert c1.camera_device == "/dev/video2"
    assert c1.camera_device_index == 2

    # Setting device path should update index
    c2 = Config(camera_device="/dev/video3")
    assert c2.camera_device == "/dev/video3"
    assert c2.camera_device_index == 3


@pytest.mark.parametrize("settings", [
    {"poll_interval_sec": 0},
    {"poll_interval_sec": "60"},
    {"daily_capture_hour": 25},
    {"daily_capture_minute": -1},
    {"stream_fps": 0},
    {"stream_max_session_sec": 0},
    {"capture_width": 0},
    {"capture_height": -1},
    {"stream_width": 0},
    {"stream_height": -1},
    {"alert_debounce_count": 0},
    {"alert_debounce_count": True},
    {"port": 65536},
    {"temp_min_warn_f": 30},
    {"temp_max_crit_f": float("nan")},
    {"temp_min_crit_f": "cold"},
])
def test_invalid_settings_rejected(settings: dict) -> None:
    with pytest.raises(ValueError):
        Config(**settings)


@pytest.mark.parametrize("yaml_text", ["- poll_interval_sec\n- 60\n", "[]", "false", "0"])
def test_config_requires_mapping(tmp_path: Path, yaml_text: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(yaml_text)
    with pytest.raises(ValueError, match="mapping"):
        load_config(path)

