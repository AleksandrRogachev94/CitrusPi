"""Camera — daily timelapse capture and on-demand MJPEG streaming.

- Daily timelapse photos are saved as:  captures/YYYY-MM-DD.jpg
- On-demand video streaming streams MJPEG at a low framerate (~5 fps)
  so the Raspberry Pi Zero 2W CPU remains cool and responsive.
- Shared V4L2 device handling prevents 'Device or resource busy' collisions.
"""

from __future__ import annotations

from collections.abc import Generator
import contextlib
from datetime import datetime
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any

from citruspi.config import Config

logger = logging.getLogger(__name__)

# Mutex to ensure simultaneous web requests and daily capture don't conflict
_CAMERA_LOCK = threading.Lock()

# Reference to active stream capture handle to allow snapshot reuse without device collisions
_active_cap: Any = None
_active_stream_count: int = 0


# Minimal 1x1 valid JPEG fallback if numpy/cv2 are not installed
_MINIMAL_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
    b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
    b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00"
    b"\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01"
    b"\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07"
    b"\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
)


def _get_placeholder_jpeg(text: str = "CitrusPi Camera Offline") -> bytes:
    """Generate a lightweight placeholder frame if device unavailable."""
    try:
        import numpy as np  # type: ignore[import-untyped]
        import cv2          # type: ignore[import-untyped]

        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        img[:] = (35, 30, 28)  # dark background
        cv2.putText(
            img, "CITRUSPI CAMERA", (100, 280), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (245, 158, 11), 3
        )
        cv2.putText(img, text, (100, 370), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (200, 200, 200), 2)
        cv2.putText(img, now_str, (100, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (120, 120, 120), 2)

        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes()
    except Exception:
        return _MINIMAL_JPEG


_DAILY_PHOTO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jpg$")

_last_capture_failure_time: float = 0.0
_FAILURE_BACKOFF_SEC: float = 600.0  # 10 minutes


def _open_video_capture(config: Config) -> Any:
    """Open OpenCV VideoCapture handle using direct V4L2 on Linux with MJPG if supported."""
    import cv2  # type: ignore[import-untyped]

    backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
    try:
        cap = cv2.VideoCapture(config.camera_device_index, backend)
    except Exception:
        cap = cv2.VideoCapture(config.camera_device_index)
    if cap.isOpened():
        with contextlib.suppress(Exception):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    return cap



def capture_frame(config: Config, filename: str) -> Path | None:
    """Capture a single JPEG from the USB camera to captures/{filename}.

    Returns the saved path or None.
    """
    global _active_cap

    config.captures_dir.mkdir(parents=True, exist_ok=True)
    target_path = config.captures_dir / filename
    tmp_path = target_path.with_name(target_path.stem + ".part.jpg")

    with _CAMERA_LOCK:
        # Mock mode fallback
        if config.mock_sensors:
            placeholder = _get_placeholder_jpeg("Mock camera frame")
            if placeholder:
                tmp_path.write_bytes(placeholder)
                os.replace(tmp_path, target_path)
                return target_path
            return None

        try:
            import cv2  # type: ignore[import-untyped]

            frame = None

            # Case A: Live stream is already open — reuse active device handle at stream resolution
            # to avoid UVC camera instability and frame drops from live resolution changes.
            if _active_cap is not None and _active_cap.isOpened():
                ret, frame = _active_cap.read()
                if not ret or frame is None:
                    logger.warning(
                        "Could not read frame from active stream handle; releasing handle"
                    )
                    _active_cap.release()
                    _active_cap = None
                    frame = None

            # Case B: Stream is not open — open device directly with auto-exposure warmup
            if frame is None:
                cap = _open_video_capture(config)
                if not cap.isOpened():
                    logger.warning("Cannot open camera device %s", config.camera_device)
                    # Do NOT poison target_path with placeholder on real hardware failure;
                    # returning None lets scheduler retry automatically.
                    return None

                try:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.capture_width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.capture_height)

                    # Discard 12 frames to allow USB webcam ISP (auto-exposure/white balance)
                    # to properly stabilize
                    for _ in range(12):
                        cap.read()

                    ret, frame = cap.read()
                    if not ret or frame is None:
                        logger.error("Failed to read frame after camera warmup")
                        return None
                finally:
                    cap.release()

            if not cv2.imwrite(str(tmp_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85]):
                logger.error("cv2.imwrite failed writing to %s", tmp_path)
                return None
            os.replace(tmp_path, target_path)
            logger.info("📸 Captured photo → %s", target_path)
            return target_path

        except ImportError:
            logger.error("opencv (cv2) not installed — cannot capture photo")
            return None
        except Exception:
            logger.exception("Error during camera capture")
            return None


def check_and_take_daily_capture(
    config: Config,
    now: datetime | None = None,
) -> Path | None:
    """Check if the scheduled daily timelapse photo has been taken today.

    If current hour/minute matches or exceeds configured time and today's
    photo does not exist yet (or is empty), take it.
    Backs off for 10 minutes on failure.
    """
    global _last_capture_failure_time

    curr_now = now or datetime.now()
    today_str = curr_now.strftime("%Y-%m-%d")
    target_path = config.captures_dir / f"{today_str}.jpg"

    if target_path.exists() and target_path.stat().st_size > 0:
        # Already snapped today's timelapse photo
        return None

    # Check failure backoff
    curr_time = time.time()
    if (curr_time - _last_capture_failure_time) < _FAILURE_BACKOFF_SEC:
        return None

    # Check if we have passed the scheduled time of day
    is_scheduled_time = curr_now.hour > config.daily_capture_hour or (
        curr_now.hour == config.daily_capture_hour
        and curr_now.minute >= config.daily_capture_minute
    )
    if is_scheduled_time:
        logger.info("⏰ Time for daily timelapse photo (%s)", today_str)
        result = capture_frame(config, filename=f"{today_str}.jpg")
        if result is None:
            _last_capture_failure_time = curr_time
            logger.warning(
                "Daily capture failed; backing off for %d seconds",
                int(_FAILURE_BACKOFF_SEC),
            )
        return result

    return None


def generate_mjpeg_stream(config: Config) -> Generator[bytes, None, None]:
    """Yield MJPEG multipart stream on-demand for live viewing in the web UI.

    Streams at ~stream_fps (default 5 FPS) to keep Pi Zero 2W CPU low.
    Releases the camera automatically when client disconnects.
    """
    global _active_cap, _active_stream_count

    fps = max(1, config.stream_fps)
    delay = 1.0 / fps

    try:
        import cv2  # type: ignore[import-untyped]
        has_cv2 = True
    except ImportError:
        has_cv2 = False

    if not has_cv2 or config.mock_sensors:
        label = "Mock Live Stream (5 FPS)" if config.mock_sensors else "OpenCV not available"
        while True:
            placeholder = _get_placeholder_jpeg(label)
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + placeholder + b"\r\n"
            )
            time.sleep(delay)

    with _CAMERA_LOCK:
        if _active_cap is None or not _active_cap.isOpened():
            _active_cap = _open_video_capture(config)
            if _active_cap.isOpened():
                _active_cap.set(cv2.CAP_PROP_FRAME_WIDTH, min(640, config.capture_width))
                _active_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, min(480, config.capture_height))

        if _active_cap is None or not _active_cap.isOpened():
            camera_ok = False
        else:
            camera_ok = True
            _active_stream_count += 1

    if not camera_ok:
        logger.warning("Camera unavailable for live stream")
        placeholder = _get_placeholder_jpeg("Camera device offline")
        while True:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + placeholder + b"\r\n"
            )
            time.sleep(1.0)

    logger.info(
        "▶ Live video stream started (%d FPS, active viewers=%d)", fps, _active_stream_count
    )
    consecutive_failures = 0
    try:
        while True:
            with _CAMERA_LOCK:
                if _active_cap is None or not _active_cap.isOpened():
                    break
                ret, frame = _active_cap.read()

            if not ret or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= 15:
                    logger.warning(
                        "Too many consecutive camera read failures (%d), terminating stream",
                        consecutive_failures,
                    )
                    with _CAMERA_LOCK:
                        if _active_cap is not None:
                            _active_cap.release()
                            _active_cap = None
                    break
                time.sleep(0.1)
                continue

            consecutive_failures = 0

            # Encode as JPEG
            ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ret:
                consecutive_failures += 1
                if consecutive_failures >= 15:
                    logger.warning("Repeated JPEG encode failure, terminating stream")
                    with _CAMERA_LOCK:
                        if _active_cap is not None:
                            _active_cap.release()
                            _active_cap = None
                    break
                time.sleep(0.1)
                continue

            frame_bytes = jpeg.tobytes()
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )
            time.sleep(delay)

    except GeneratorExit:
        logger.info("⏹ Live video stream stopped for a client")
    finally:
        with _CAMERA_LOCK:
            _active_stream_count = max(0, _active_stream_count - 1)
            if _active_stream_count == 0 and _active_cap is not None:
                logger.info("Releasing camera device handle (no active viewers)")
                _active_cap.release()
                _active_cap = None


def list_photos(config: Config) -> list[dict[str, str]]:
    """Return list of all daily photos sorted chronologically for timelapse."""
    if not config.captures_dir.exists():
        return []

    photos = []
    # Match YYYY-MM-DD.jpg strictly
    for f in sorted(config.captures_dir.glob("*.jpg")):
        if not _DAILY_PHOTO_RE.match(f.name):
            continue
        date_str = f.stem
        mtime = int(f.stat().st_mtime)
        photos.append({
            "filename": f.name,
            "date": date_str,
            "url": f"/captures/{f.name}?v={mtime}",
        })
    return photos


def latest_photo(config: Config) -> dict[str, str] | None:
    """Return the most recent photo."""
    photos = list_photos(config)
    return photos[-1] if photos else None
