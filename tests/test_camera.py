"""Tests for camera and daily capture logic."""

from datetime import datetime
from pathlib import Path

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


def test_list_photos_ignores_non_date_files(tmp_path: Path) -> None:
    cfg = Config(captures_dir=tmp_path / "captures")
    cfg.captures_dir.mkdir(parents=True)
    (cfg.captures_dir / "2026-10-13.jpg").write_bytes(b"dummy")
    (cfg.captures_dir / "manual.jpg").write_bytes(b"dummy")
    (cfg.captures_dir / "debug.jpg").write_bytes(b"dummy")

    photos = camera.list_photos(cfg)
    assert len(photos) == 1
    assert photos[0]["filename"] == "2026-10-13.jpg"


def test_check_and_take_daily_capture_schedule(tmp_path: Path) -> None:
    cfg = Config(
        captures_dir=tmp_path / "captures",
        mock_sensors=True,
        daily_capture_hour=22,
        daily_capture_minute=0,
    )
    # Before scheduled time: should return None
    before = datetime(2026, 10, 14, 15, 30)
    assert camera.check_and_take_daily_capture(cfg, now=before) is None

    # After scheduled time: should take photo
    after = datetime(2026, 10, 14, 22, 5)
    captured = camera.check_and_take_daily_capture(cfg, now=after)
    assert captured is not None
    assert captured.name == "2026-10-14.jpg"

    # Already taken today: should return None
    assert camera.check_and_take_daily_capture(cfg, now=after) is None

