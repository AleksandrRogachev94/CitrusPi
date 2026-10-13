"""Camera — daily timelapse capture and on-demand MJPEG streaming.

- Daily timelapse photos are saved as:  captures/YYYY-MM-DD.jpg
- On-demand video streaming streams MJPEG at a low framerate (~5 fps)
  so the Raspberry Pi Zero 2W CPU remains cool and responsive.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from citruspi.config import Config

logger = logging.getLogger(__name__)

# Mutex to ensure simultaneous web requests and daily capture don't conflict
_CAMERA_LOCK = threading.Lock()


def _get_placeholder_jpeg(text: str = "CitrusPi Camera Offline") -> bytes:
    """Generate a lightweight SVG-turned-JPEG or mock frame if device unavailable."""
    # Try using cv2 to draw a clean placeholder frame
    try:
        import numpy as np  # type: ignore[import-untyped]
        import cv2          # type: ignore[import-untyped]

        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        img[:] = (35, 30, 28)  # dark background
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cv2.putText(img, "CITRUSPI CAMERA", (100, 280), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (245, 158, 11), 3)
        cv2.putText(img, text, (100, 370), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (200, 200, 200), 2)
        cv2.putText(img, now_str, (100, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (120, 120, 120), 2)

        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes()
    except Exception:
        # Minimal fallback header
        return b""


def capture_frame(config: Config, filename: str | None = None) -> Path | None:
    """Capture a single JPEG from the USB camera.

    Saves to captures/{filename} or captures/YYYY-MM-DD.jpg.
    Returns the saved path or None.
    """
    config.captures_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    target_name = filename or f"{today_str}.jpg"
    target_path = config.captures_dir / target_name

    with _CAMERA_LOCK:
        try:
            import cv2  # type: ignore[import-untyped]

            cap = cv2.VideoCapture(config.camera_device_index)
            if not cap.isOpened():
                logger.warning("Cannot open camera device %s", config.camera_device)
                # Write placeholder for dev
                placeholder = _get_placeholder_jpeg("No camera device found at /dev/video0")
                if placeholder:
                    target_path.write_bytes(placeholder)
                    return target_path
                return None

            try:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.capture_width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.capture_height)

                # Discard first frame for auto-exposure adjustment
                cap.read()
                ret, frame = cap.read()
                if not ret or frame is None:
                    logger.error("Failed to read frame from camera")
                    return None

                cv2.imwrite(str(target_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                logger.info("📸 Captured photo → %s", target_path)
                return target_path
            finally:
                cap.release()

        except ImportError:
            logger.error("opencv (cv2) not installed — cannot capture photo")
            placeholder = _get_placeholder_jpeg("OpenCV (cv2) not installed")
            if placeholder:
                target_path.write_bytes(placeholder)
                return target_path
            return None
        except Exception:
            logger.exception("Error during camera capture")
            return None


def check_and_take_daily_capture(config: Config) -> Path | None:
    """Check if the scheduled daily timelapse photo has been taken today.

    If current hour/minute matches or exceeds configured time and today's
    photo does not exist yet, take it.
    """
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    target_path = config.captures_dir / f"{today_str}.jpg"

    if target_path.exists():
        # Already snapped today's timelapse photo
        return None

    # Check if we have passed the scheduled time of day
    if now.hour > config.daily_capture_hour or (
        now.hour == config.daily_capture_hour and now.minute >= config.daily_capture_minute
    ):
        logger.info("⏰ Time for daily timelapse photo (%s)", today_str)
        return capture_frame(config, filename=f"{today_str}.jpg")

    return None


def generate_mjpeg_stream(config: Config) -> Generator[bytes, None, None]:
    """Yield MJPEG multipart stream on-demand for live viewing in the web UI.

    Streams at ~stream_fps (default 5 FPS) to keep Pi Zero 2W CPU low.
    Releases the camera automatically when client disconnects.
    """
    fps = max(1, config.stream_fps)
    delay = 1.0 / fps

    try:
        import cv2  # type: ignore[import-untyped]
        has_cv2 = True
    except ImportError:
        has_cv2 = False

    if not has_cv2:
        placeholder = _get_placeholder_jpeg("OpenCV not available")
        while True:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + placeholder + b"\r\n"
            )
            time.sleep(1.0)

    # Open camera with lock
    cap = None
    try:
        with _CAMERA_LOCK:
            cap = cv2.VideoCapture(config.camera_device_index)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, min(640, config.capture_width))
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, min(480, config.capture_height))

        if cap is None or not cap.isOpened():
            logger.warning("Camera unavailable for live stream")
            placeholder = _get_placeholder_jpeg("Camera device offline")
            while True:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + placeholder + b"\r\n"
                )
                time.sleep(1.0)

        logger.info("▶ Live video stream started (%d FPS)", fps)
        while True:
            with _CAMERA_LOCK:
                ret, frame = cap.read()

            if not ret or frame is None:
                time.sleep(0.1)
                continue

            # Encode as JPEG
            ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ret:
                time.sleep(0.1)
                continue

            frame_bytes = jpeg.tobytes()
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )
            time.sleep(delay)

    except GeneratorExit:
        logger.info("⏹ Live video stream stopped (client disconnected)")
    finally:
        if cap is not None and cap.isOpened():
            with _CAMERA_LOCK:
                cap.release()


def list_photos(config: Config) -> list[dict[str, str]]:
    """Return list of all daily photos sorted chronologically for timelapse."""
    if not config.captures_dir.exists():
        return []

    photos = []
    # Match YYYY-MM-DD.jpg
    for f in sorted(config.captures_dir.glob("*.jpg")):
        date_str = f.stem
        photos.append({
            "filename": f.name,
            "date": date_str,
            "url": f"/captures/{f.name}",
        })
    return photos


def latest_photo(config: Config) -> dict[str, str] | None:
    """Return the most recent photo."""
    photos = list_photos(config)
    return photos[-1] if photos else None
