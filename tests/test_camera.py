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


def test_capture_placeholder_in_mock_mode(tmp_path: Path) -> None:
    cfg = Config(captures_dir=tmp_path / "captures", mock_sensors=True)
    path = camera.capture_frame(cfg, filename="2026-10-13.jpg")
    assert path is not None
    assert path.exists()

    photos = camera.list_photos(cfg)
    assert len(photos) == 1
    assert photos[0]["date"] == "2026-10-13"


def test_capture_returns_none_when_real_device_missing(tmp_path: Path) -> None:
    cfg = Config(
        captures_dir=tmp_path / "captures",
        mock_sensors=False,
        camera_device_index=99,  # non-existent V4L2 index
    )
    path = camera.capture_frame(cfg, filename="2026-10-13.jpg")
    assert path is None
    assert not (cfg.captures_dir / "2026-10-13.jpg").exists()


def test_generate_mjpeg_stream_mock(tmp_path: Path) -> None:
    cfg = Config(captures_dir=tmp_path / "captures", mock_sensors=True, stream_fps=5)
    stream = camera.generate_mjpeg_stream(cfg)
    frame = next(stream)
    assert b"--frame" in frame
    assert b"image/jpeg" in frame
    stream.close()

