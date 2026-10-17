"""Configuration — simple dataclass with YAML overlay."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class Config:
    # ── Sensor polling ────────────────────────────────────────────────
    poll_interval_sec: int = 60  # 1 min default for high resolution
    bme280_address: int = 0x76
    w1_devices_dir: Path = Path("/sys/bus/w1/devices")
    mock_sensors: bool = False  # explicitly enabled for local development

    # ── Camera & Photoperiod ──────────────────────────────────────────
    camera_device: str = "/dev/video0"
    camera_device_index: int = 0
    capture_width: int = 1920
    capture_height: int = 1080

    # Photoperiod & daily timelapse photo schedule
    # Citrus grow tents often run lights overnight (e.g. 20:00 to 06:00)
    # Daily photo is snapped once per day during lights-on for the multi-month timelapse.
    daily_capture_hour: int = 22     # 22:00 (10 PM)
    daily_capture_minute: int = 0
    stream_fps: int = 5              # 5 fps live stream preserves Pi Zero 2W CPU
    stream_max_session_sec: int = 300  # Max continuous live stream duration (5 min)

    # ── Storage ───────────────────────────────────────────────────────
    data_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "data")
    captures_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "captures")

    # ── Alerts & Heartbeat ────────────────────────────────────────────
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    heartbeat_url: str = ""
    temp_min_crit_f: float = 38.0
    temp_min_warn_f: float = 44.0
    temp_max_crit_f: float = 85.0
    alert_debounce_count: int = 3

    # ── AI Analysis ───────────────────────────────────────────────────
    ai_enabled: bool = False
    ai_api_key: str = ""
    ai_model: str = "gemini-3.8-flash"
    plant_layout: str = ""  # must match the camera framing

    # ── Web server ────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 80
    log_level: str = "info"

    def __post_init__(self) -> None:
        bounds = {
            "poll_interval_sec": (1, None),
            "daily_capture_hour": (0, 23),
            "daily_capture_minute": (0, 59),
            "stream_fps": (1, None),
            "stream_max_session_sec": (1, None),
            "capture_width": (1, None),
            "capture_height": (1, None),
            "camera_device_index": (0, None),
            "alert_debounce_count": (1, None),
            "port": (1, 65535),
        }
        for name, (minimum, maximum) in bounds.items():
            value = getattr(self, name)
            if (
                type(value) is not int
                or value < minimum
                or (maximum is not None and value > maximum)
            ):
                upper_bound = f" and <= {maximum}" if maximum is not None else ""
                raise ValueError(f"{name} must be an integer >= {minimum}{upper_bound}")
        thresholds = (self.temp_min_crit_f, self.temp_min_warn_f, self.temp_max_crit_f)
        if any(
            type(value) not in (int, float) or not math.isfinite(value)
            for value in thresholds
        ):
            raise ValueError("Temperature thresholds must be finite numbers")
        if not self.temp_min_crit_f < self.temp_min_warn_f < self.temp_max_crit_f:
            raise ValueError("Temperature thresholds must satisfy critical low < warning < high")

        if isinstance(self.w1_devices_dir, str):
            self.w1_devices_dir = Path(self.w1_devices_dir)
        if isinstance(self.data_dir, str):
            self.data_dir = Path(self.data_dir)
        if isinstance(self.captures_dir, str):
            self.captures_dir = Path(self.captures_dir)

        # Synchronize camera_device and camera_device_index to prevent split-brain
        if self.camera_device_index != 0 and self.camera_device == "/dev/video0":
            self.camera_device = f"/dev/video{self.camera_device_index}"
        elif self.camera_device != "/dev/video0":
            match = re.search(r"video(\d+)$", str(self.camera_device))
            if match:
                self.camera_device_index = int(match.group(1))


def load_config(path: str | Path | None = None) -> Config:
    """Load config from YAML, env-var, or compiled defaults."""
    raw: dict[str, Any] = {}
    if path is not None:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(p) as fh:
            loaded = yaml.safe_load(fh)
            raw = {} if loaded is None else loaded
    else:
        search: list[Path] = []
        env = os.environ.get("CITRUSPI_CONFIG")
        if env:
            search.append(Path(env))
        search.append(_PROJECT_ROOT / "config.yaml")

        for p in search:
            if p.is_file():
                with open(p) as fh:
                    loaded = yaml.safe_load(fh)
                    raw = {} if loaded is None else loaded
                break  # first match wins

    import dataclasses
    import logging

    logger = logging.getLogger(__name__)

    if not isinstance(raw, dict):
        raise ValueError("Config YAML must contain a mapping of settings")

    # Convert paths
    for key in ("data_dir", "captures_dir", "w1_devices_dir"):
        if key in raw and raw[key] is not None:
            raw[key] = Path(raw[key])

    # Environment variable overrides
    if "CITRUSPI_NTFY_TOPIC" in os.environ:
        raw["ntfy_topic"] = os.environ["CITRUSPI_NTFY_TOPIC"]
    if "CITRUSPI_HEARTBEAT_URL" in os.environ:
        raw["heartbeat_url"] = os.environ["CITRUSPI_HEARTBEAT_URL"]
    if "CITRUSPI_AI_API_KEY" in os.environ:
        raw["ai_api_key"] = os.environ["CITRUSPI_AI_API_KEY"]

    valid_fields = {f.name for f in dataclasses.fields(Config)}
    filtered: dict[str, Any] = {}
    for k, v in raw.items():
        if k in valid_fields:
            filtered[k] = v
        else:
            logger.warning("Ignoring unrecognized config key: '%s'", k)

    return Config(**filtered)
