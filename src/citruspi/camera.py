"""Camera — periodic JPEG capture, organised by date for timelapse.

Photos are saved to:  captures/YYYY-MM-DD/HHMMSS.jpg
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from citruspi.config import Config

logger = logging.getLogger(__name__)


def is_daylight(config: Config, now: datetime | None = None) -> bool:
    """True if current hour falls within the LED photoperiod."""
    now = now or datetime.now()
    return config.lights_on_hour <= now.hour < config.lights_off_hour


def capture_frame(config: Config, *, force: bool = False) -> Path | None:
    """Snap a single JPEG.  Returns the saved path, or None if skipped."""
    if not force and not is_daylight(config):
        logger.debug("Dark phase — capture skipped")
        return None

    try:
        import cv2  # type: ignore[import-untyped]
    except ImportError:
        logger.error("opencv not installed — cannot capture")
        return None

    cap = cv2.VideoCapture(config.camera_device_index)
    if not cap.isOpened():
        logger.error("Cannot open camera device %s", config.camera_device)
        return None

    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.capture_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.capture_height)

        ret, frame = cap.read()
        if not ret or frame is None:
            logger.error("Failed to read frame")
            return None

        now = datetime.now()
        day_dir = config.captures_dir / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        filepath = day_dir / now.strftime("%H%M%S.jpg")

        cv2.imwrite(str(filepath), frame)
        logger.info("📸 Captured → %s", filepath)
        return filepath
    finally:
        cap.release()


def list_dates(config: Config) -> list[str]:
    """Return sorted list of YYYY-MM-DD date strings that have captures."""
    if not config.captures_dir.exists():
        return []
    dates = [
        d.name
        for d in sorted(config.captures_dir.iterdir())
        if d.is_dir() and len(d.name) == 10
    ]
    return dates


def list_photos(config: Config, date: str) -> list[dict[str, str]]:
    """Return list of photo metadata for a given date."""
    day_dir = config.captures_dir / date
    if not day_dir.exists():
        return []
    photos = []
    for f in sorted(day_dir.glob("*.jpg")):
        time_str = f.stem  # HHMMSS
        display_time = f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}" if len(time_str) == 6 else time_str
        photos.append({
            "filename": f.name,
            "date": date,
            "time": display_time,
            "url": f"/captures/{date}/{f.name}",
        })
    return photos


def latest_photo(config: Config) -> dict[str, str] | None:
    """Return metadata for the most recent photo, or None."""
    dates = list_dates(config)
    if not dates:
        return None
    photos = list_photos(config, dates[-1])
    return photos[-1] if photos else None
