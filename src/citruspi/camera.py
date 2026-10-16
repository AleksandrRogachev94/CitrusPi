"""Camera — daily timelapse capture and on-demand MJPEG streaming.

- Daily timelapse photos are saved as:  captures/YYYY-MM-DD.jpg
- On-demand video streaming streams MJPEG at a low framerate (~5 fps)
  so the Raspberry Pi Zero 2W CPU remains cool and responsive.
- Shared V4L2 device handling prevents 'Device or resource busy' collisions.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
import threading
import time
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import Any

from citruspi.config import Config

logger = logging.getLogger(__name__)

# Reentrant mutex for camera operations (avoids deadlock if GC finalizer runs on same thread)
_CAMERA_LOCK = threading.RLock()

# Reference to active stream capture handle to allow snapshot reuse without device collisions
_active_cap: Any = None
_last_stream_activity: float = 0.0  # time.monotonic()
_STREAM_IDLE_TIMEOUT_SEC: float = 5.0
_reaper_started: bool = False


def _mjpg_fourcc(cv2_mod: Any) -> int:
    """Resolve MJPG fourcc code across OpenCV variants without eager evaluation."""
    if hasattr(cv2_mod, "VideoWriter_fourcc"):
        return int(cv2_mod.VideoWriter_fourcc(*"MJPG"))
    if hasattr(cv2_mod, "VideoWriter") and hasattr(cv2_mod.VideoWriter, "fourcc"):
        return int(cv2_mod.VideoWriter.fourcc(*"MJPG"))
    return ord("M") | (ord("J") << 8) | (ord("P") << 16) | (ord("G") << 24)


def _touch_stream() -> None:
    """Update stream lease timestamp to extend camera handle retention."""
    global _last_stream_activity
    _last_stream_activity = time.monotonic()


def _release_stream_handle_locked() -> None:
    """Release active stream capture handle. Caller MUST hold _CAMERA_LOCK."""
    global _active_cap
    if _active_cap is not None:
        with contextlib.suppress(Exception):
            _active_cap.release()
        _active_cap = None


def _release_if_idle() -> None:
    """Release active capture handle if stream has been idle past timeout."""
    with _CAMERA_LOCK:
        if (
            _active_cap is not None
            and (time.monotonic() - _last_stream_activity) > _STREAM_IDLE_TIMEOUT_SEC
        ):
            logger.info("Releasing idle camera stream handle (no stream activity)")
            _release_stream_handle_locked()


def _reaper_loop() -> None:
    """Background daemon loop periodically checking for idle camera streams."""
    while True:
        time.sleep(1.0)
        _release_if_idle()


def _ensure_reaper() -> None:
    """Ensure background stream reaper thread is running."""
    global _reaper_started
    with _CAMERA_LOCK:
        if not _reaper_started:
            thread = threading.Thread(
                target=_reaper_loop,
                daemon=True,
                name="citruspi-stream-reaper",
            )
            thread.start()
            _reaper_started = True


def _acquire_stream_handle(config: Config) -> bool:
    """Acquire or touch active capture handle for MJPEG streaming.

    Returns True if an open handle is available, False otherwise.
    Never leaves an open handle in _active_cap on failure.
    """
    global _active_cap
    with _CAMERA_LOCK:
        if _active_cap is not None and getattr(_active_cap, "isOpened", lambda: False)():
            _touch_stream()
            return True

        _release_stream_handle_locked()

        new_cap = None
        try:
            import cv2  # type: ignore[import-untyped]

            new_cap = _open_video_capture(config)
            if not new_cap or not getattr(new_cap, "isOpened", lambda: False)():
                if new_cap is not None:
                    with contextlib.suppress(Exception):
                        new_cap.release()
                return False

            new_cap.set(cv2.CAP_PROP_FRAME_WIDTH, min(640, config.capture_width))
            new_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, min(480, config.capture_height))
            try:
                new_cap.set(cv2.CAP_PROP_FOURCC, _mjpg_fourcc(cv2))
            except Exception as exc:
                logger.debug("Could not set MJPG fourcc on stream handle: %s", exc)

            _active_cap = new_cap
            _touch_stream()
            _ensure_reaper()
            return True
        except Exception:
            if new_cap is not None:
                with contextlib.suppress(Exception):
                    new_cap.release()
            return False


def _read_stream_frame() -> Any:
    """Read next frame from active stream handle with lease touch."""
    with _CAMERA_LOCK:
        if _active_cap is None or not getattr(_active_cap, "isOpened", lambda: False)():
            return None
        ret, frame = _active_cap.read()
        if ret and frame is not None:
            _touch_stream()
            return frame
        return None


def _multipart_part(jpeg: bytes) -> bytes:
    """Format JPEG bytes into a standard MJPEG multipart boundary."""
    return b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"


def _placeholder_stream(
    label: str,
    delay: float,
    max_session: float,
    regenerate: bool = False,
) -> Generator[bytes, None, None]:
    """Yield placeholder frames until max_session duration expires."""
    started = time.monotonic()
    frame_bytes = _get_placeholder_jpeg(label) if not regenerate else b""
    while (time.monotonic() - started) < max_session:
        if regenerate:
            frame_bytes = _get_placeholder_jpeg(label)
        yield _multipart_part(frame_bytes)
        time.sleep(delay)


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
        import cv2  # type: ignore[import-untyped]
        import numpy as np  # type: ignore[import-untyped]

        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        img[:] = (35, 30, 28)  # dark background
        cv2.putText(
            img, "CITRUSPI CAMERA", (100, 280), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (245, 158, 11), 3
        )
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
        try:
            cap.set(cv2.CAP_PROP_FOURCC, _mjpg_fourcc(cv2))
        except Exception as exc:
            logger.debug("Could not set MJPG fourcc on camera device: %s", exc)
        with contextlib.suppress(Exception):
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
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
            is_daily = filename != "manual.jpg"

            # Case A: Live stream reuse is ONLY used for manual ad-hoc snapshots.
            # Daily scheduled timelapse photos ALWAYS release the stream handle to ensure
            # full configured resolution (e.g. 1080p) and 25-frame auto-exposure warmup.
            # The live stream automatically re-acquires the device on its next iteration.
            if is_daily:
                _release_stream_handle_locked()
            elif _active_cap is not None and getattr(_active_cap, "isOpened", lambda: False)():
                ret, frame = _active_cap.read()
                if not ret or frame is None:
                    logger.warning(
                        "Could not read frame from active stream handle; releasing handle"
                    )
                    _release_stream_handle_locked()
                    frame = None

            # Case B: Stream is not open (or was released for daily photo)
            # — open device directly with warmup
            if frame is None:
                cap = _open_video_capture(config)
                if not cap.isOpened():
                    logger.warning("Cannot open camera device %s", config.camera_device)
                    # Do NOT poison target_path with placeholder on real hardware failure;
                    # returning None lets scheduler retry automatically.
                    return None

                try:
                    with contextlib.suppress(Exception):
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.capture_width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.capture_height)

                    # Re-assert MJPG fourcc after setting resolution (required by some V4L2)
                    try:
                        cap.set(cv2.CAP_PROP_FOURCC, _mjpg_fourcc(cv2))
                    except Exception as exc:
                        logger.debug("Could not set MJPG fourcc on capture: %s", exc)

                    # Discard 25 frames (~1 sec at 30 fps) to allow USB webcam ISP
                    # (auto-exposure, auto-white balance, and motorized autofocus)
                    # to properly settle before snapping.
                    for _ in range(25):
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
            h, w = frame.shape[:2]
            size_kb = target_path.stat().st_size / 1024
            logger.info("📸 Captured photo (%dx%d, %.1f KB) → %s", w, h, size_kb, target_path)
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
    Releases the camera automatically via idle reaper when client disconnects.
    """
    fps = max(1, config.stream_fps)
    delay = 1.0 / fps
    max_session = float(config.stream_max_session_sec)

    try:
        import cv2  # type: ignore[import-untyped]
        has_cv2 = True
    except ImportError:
        has_cv2 = False

    if not has_cv2 or config.mock_sensors:
        label = "Mock Live Stream (5 FPS)" if config.mock_sensors else "OpenCV not available"
        yield from _placeholder_stream(
            label=label,
            delay=delay,
            max_session=max_session,
            regenerate=config.mock_sensors,
        )
        return

    if not _acquire_stream_handle(config):
        logger.warning("Camera unavailable for live stream")
        yield from _placeholder_stream(
            label="Camera device offline",
            delay=1.0,
            max_session=max_session,
            regenerate=False,
        )
        return

    logger.info("▶ Live video stream started (%d FPS)", fps)
    consecutive_failures = 0
    started = time.monotonic()
    try:
        while (time.monotonic() - started) < max_session:
            frame = _read_stream_frame()
            if frame is None:
                _acquire_stream_handle(config)
                consecutive_failures += 1
                if consecutive_failures >= 15:
                    logger.warning(
                        "Too many consecutive camera read failures (%d), terminating stream",
                        consecutive_failures,
                    )
                    break
                time.sleep(0.1)
                continue

            # Encode as JPEG
            ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ret:
                consecutive_failures += 1
                if consecutive_failures >= 15:
                    logger.warning("Repeated JPEG encode failure, terminating stream")
                    break
                time.sleep(0.1)
                continue

            consecutive_failures = 0
            yield _multipart_part(jpeg.tobytes())
            time.sleep(delay)

    except GeneratorExit:
        logger.info("⏹ Live video stream stopped for a client")
    finally:
        logger.info("Live video stream ended")


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
