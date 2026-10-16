import time
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from citruspi import camera
from citruspi.config import Config


class FakeCap:
    """Mock OpenCV VideoCapture for unit testing stream lifecycle."""

    def __init__(self, opened: bool = True, frames: list[Any] | None = None) -> None:
        self.opened = opened
        self.frames = frames if frames is not None else [object()]
        self.frame_idx = 0
        self.set_calls: list[tuple[int, Any]] = []
        self.release_count = 0
        self.read_count = 0

    def isOpened(self) -> bool:
        return self.opened

    def set(self, prop: int, value: Any) -> bool:
        self.set_calls.append((prop, value))
        return True

    def read(self) -> tuple[bool, Any]:
        self.read_count += 1
        if not self.opened or not self.frames:
            return False, None
        frame = self.frames[min(self.frame_idx, len(self.frames) - 1)]
        self.frame_idx += 1
        return True, frame

    def release(self) -> None:
        self.release_count += 1
        self.opened = False


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
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    with patch("cv2.VideoCapture", return_value=mock_cap):
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


def test_release_if_idle_timing() -> None:
    fake = FakeCap(opened=True)
    with camera._CAMERA_LOCK:
        camera._active_cap = fake
    camera._touch_stream()

    # Before idle timeout: should NOT release
    camera._release_if_idle()
    assert camera._active_cap is fake
    assert fake.release_count == 0

    # After idle timeout: SHOULD release
    camera._last_stream_activity = (
        time.monotonic() - camera._STREAM_IDLE_TIMEOUT_SEC - 1.0
    )
    camera._release_if_idle()
    assert camera._active_cap is None
    assert fake.release_count == 1


def test_abandoned_generator_released_by_idle_reaper(tmp_path: Path) -> None:
    cfg = Config(
        captures_dir=tmp_path / "captures",
        mock_sensors=False,
        stream_fps=5,
    )
    fake_cap = FakeCap(opened=True)

    with (
        patch("citruspi.camera._open_video_capture", return_value=fake_cap),
        patch("cv2.imencode", return_value=(True, MagicMock(tobytes=lambda: b"fake-frame"))),
    ):
        gen = camera.generate_mjpeg_stream(cfg)
        part = next(gen)
        assert b"--frame" in part
        assert camera._active_cap is fake_cap
        assert fake_cap.release_count == 0

        # Abandon generator (never closed, client disconnected)
        camera._last_stream_activity = (
            time.monotonic() - camera._STREAM_IDLE_TIMEOUT_SEC - 1.0
        )
        camera._release_if_idle()

        assert fake_cap.release_count == 1
        assert camera._active_cap is None


def test_failed_open_never_leaves_open_handle_in_active_cap(tmp_path: Path) -> None:
    cfg = Config(captures_dir=tmp_path / "captures", mock_sensors=False)

    # Subtest 1: Device fails to open (isOpened is False)
    unopened_cap = FakeCap(opened=False)
    with patch("citruspi.camera._open_video_capture", return_value=unopened_cap):
        assert camera._acquire_stream_handle(cfg) is False
        assert camera._active_cap is None
        assert unopened_cap.release_count == 1

    # Subtest 2: Exception raised during cap configuration
    faulty_cap = FakeCap(opened=True)

    def faulty_set(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("V4L2 device fault")

    faulty_cap.set = faulty_set  # type: ignore[assignment]
    with patch("citruspi.camera._open_video_capture", return_value=faulty_cap):
        assert camera._acquire_stream_handle(cfg) is False
        assert camera._active_cap is None
        assert faulty_cap.release_count == 1


def test_generator_ends_after_max_session_sec(tmp_path: Path) -> None:
    cfg = Config(
        captures_dir=tmp_path / "captures",
        mock_sensors=False,
        stream_fps=10,
        stream_max_session_sec=10,
    )
    fake_cap = FakeCap(opened=True)

    monotonic_times = [100.0, 100.1, 100.2, 115.0]  # jumps past max_session_sec (10s)
    time_idx = 0

    def mock_monotonic() -> float:
        nonlocal time_idx
        val = monotonic_times[min(time_idx, len(monotonic_times) - 1)]
        time_idx += 1
        return val

    with (
        patch("citruspi.camera._open_video_capture", return_value=fake_cap),
        patch("cv2.imencode", return_value=(True, MagicMock(tobytes=lambda: b"fake-frame"))),
        patch("time.monotonic", side_effect=mock_monotonic),
        patch("time.sleep"),
    ):
        gen = camera.generate_mjpeg_stream(cfg)
        parts = list(gen)
        # Yields frames until time jumps past max_session_sec and cleanly terminates
        assert len(parts) >= 1


