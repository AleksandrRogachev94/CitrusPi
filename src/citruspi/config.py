"""Configuration — simple dataclass with YAML overlay."""

from __future__ import annotations

import os
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
    soil_moisture_address: int = 0x36
    mock_sensors: bool = False  # auto-detected if hardware unavailable

    # ── Camera & Photoperiod ──────────────────────────────────────────
    camera_device: str = "/dev/video0"
    camera_device_index: int = 0
    capture_width: int = 1280
    capture_height: int = 720

    # Photoperiod & daily timelapse photo schedule
    # Citrus grow tents often run lights overnight (e.g. 20:00 to 06:00)
    # Daily photo is snapped once per day during lights-on for the multi-month timelapse.
    daily_capture_hour: int = 22     # 22:00 (10 PM)
    daily_capture_minute: int = 0
    stream_fps: int = 5              # 5 fps live stream preserves Pi Zero 2W CPU

    # ── Storage ───────────────────────────────────────────────────────
    data_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "data")
    captures_dir: Path = field(default_factory=lambda: _PROJECT_ROOT / "captures")

    # ── Web server ────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"


def load_config(path: str | Path | None = None) -> Config:
    """Load config from YAML, env-var, or compiled defaults."""
    search: list[Path] = []
    if path:
        search.append(Path(path))
    env = os.environ.get("CITRUSPI_CONFIG")
    if env:
        search.append(Path(env))
    search.append(_PROJECT_ROOT / "config.yaml")

    raw: dict[str, Any] = {}
    for p in search:
        if p.is_file():
            with open(p) as fh:
                raw = yaml.safe_load(fh) or {}
            break  # first match wins

    # Convert paths
    for key in ("data_dir", "captures_dir"):
        if key in raw:
            raw[key] = Path(raw[key])

    return Config(**raw)
