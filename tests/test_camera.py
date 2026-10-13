"""Tests for camera and daily capture logic."""

from datetime import datetime
from pathlib import Path

import pytest

from citruspi import camera
from citruspi.config import Config


def test_list_photos_empty(tmp_path: Path) -> None:
    cfg = Config(captures_dir=tmp_path / "captures")
    photos = camera.list_photos(cfg)
    assert photos == []


def test_capture_placeholder_when_no_device(tmp_path: Path) -> None:
    cfg = Config(captures_dir=tmp_path / "captures")
    path = camera.capture_frame(cfg, filename="2026-10-13.jpg")
    # Even without a real camera, fallback writes a placeholder for dev
    assert path is not None
    assert path.exists()

    photos = camera.list_photos(cfg)
    assert len(photos) == 1
    assert photos[0]["date"] == "2026-10-13"
