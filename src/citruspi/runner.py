"""Background task runners — sensor polling and scheduled daily timelapse & analysis."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from citruspi import camera
from citruspi.alerts import AlertManager
from citruspi.analyzer import (
    PermanentAIError,
    check_frame_brightness,
    run_daily_analysis,
)
from citruspi.config import Config
from citruspi.models import Reading
from citruspi.sensors import SensorHub
from citruspi.store import Store

logger = logging.getLogger(__name__)

_last_ai_failure_time: float = 0.0
_AI_BACKOFF_SEC: float = 600.0  # 10 minutes
_MAX_AI_DAILY_ATTEMPTS: int = 3
_ai_daily_attempts: int = 0
_ai_attempt_date: str = ""
_ai_permanent_failure_date: str = ""
_last_dark_alert_date: str = ""
_last_brightness_check_date: str = ""


def sensor_tick(
    sensors: SensorHub,
    store: Store,
    alerts: AlertManager,
) -> Reading:
    """Execute a single sensor poll cycle: read -> store -> evaluate alerts -> heartbeat."""
    reading = sensors.read()
    store.insert(reading)
    logger.debug("📊 Poll: %s", reading)

    try:
        alerts.evaluate(reading)
    except Exception:
        logger.exception("Alert evaluation error")

    try:
        alerts.send_heartbeat()
    except Exception:
        logger.exception("Heartbeat error")

    return reading


def daily_tick(
    config: Config,
    store: Store,
    now: datetime | None = None,
    alerts: AlertManager | None = None,
) -> Path | None:
    """Execute a single daily timelapse check: schedule -> capture -> AI analysis."""
    global _last_ai_failure_time, _ai_daily_attempts, _ai_attempt_date, _ai_permanent_failure_date
    global _last_dark_alert_date, _last_brightness_check_date

    curr_now = now or datetime.now()
    today_str = curr_now.strftime("%Y-%m-%d")

    photo_path = camera.check_and_take_daily_capture(config, now=curr_now)
    if photo_path:
        logger.info("Daily timelapse capture taken: %s", photo_path)

    target_path = photo_path or (config.captures_dir / f"{today_str}.jpg")
    if (
        target_path.is_file()
        and alerts is not None
        and _last_brightness_check_date != today_str
    ):
        _last_brightness_check_date = today_str
        try:
            photo_bytes = target_path.read_bytes()
            if not check_frame_brightness(photo_bytes) and _last_dark_alert_date != today_str:
                logger.warning(
                    "Daily photo %s is dark — alerting grow light failure", target_path
                )
                if alerts.send_ntfy(
                    title="WARNING: Grow Light Failure Suspected",
                    message=(
                        f"Daily photo taken at {config.daily_capture_hour:02d}:"
                        f"{config.daily_capture_minute:02d} is too dark. "
                        "Check tent grow lights, timers, or power."
                    ),
                    priority="high",
                    tags="warning",
                ):
                    _last_dark_alert_date = today_str
        except Exception:
            logger.exception("Error checking frame brightness for dark alert")

    if config.ai_enabled and config.ai_api_key and target_path.is_file():
        # Reset attempt counter when date rolls over
        if _ai_attempt_date != today_str:
            _ai_attempt_date = today_str
            _ai_daily_attempts = 0

        latest_rep = store.latest_report()
        already_analyzed = latest_rep is not None and latest_rep.get("date") == today_str
        is_permanent_today = _ai_permanent_failure_date == today_str
        attempts_exhausted = _ai_daily_attempts >= _MAX_AI_DAILY_ATTEMPTS

        if (
            not already_analyzed
            and not is_permanent_today
            and not attempts_exhausted
            and (time.time() - _last_ai_failure_time) >= _AI_BACKOFF_SEC
        ):
            _ai_daily_attempts += 1
            try:
                result = run_daily_analysis(config, store, target_path)
                if result is None:
                    _last_ai_failure_time = time.time()
                    logger.warning(
                        "Daily AI analysis returned None (attempt %d/%d); backing off for %ds",
                        _ai_daily_attempts,
                        _MAX_AI_DAILY_ATTEMPTS,
                        int(_AI_BACKOFF_SEC),
                    )
            except PermanentAIError as exc:
                _ai_permanent_failure_date = today_str
                _last_ai_failure_time = time.time()
                logger.error(
                    "Permanent AI failure for %s (%s). Halting AI retries for today.",
                    today_str,
                    exc,
                )
            except Exception:
                _last_ai_failure_time = time.time()
                logger.exception("Daily AI analysis unexpected error")

    return photo_path


def run_sensor_loop(
    config: Config,
    sensors: SensorHub,
    store: Store,
    alerts: AlertManager,
    stop_event: threading.Event,
) -> None:
    """Background thread loop for periodic sensor polling."""
    logger.info(
        "📊 Sensor polling active (every %ds, mock=%s)",
        config.poll_interval_sec,
        sensors.is_mock,
    )
    # Take initial reading immediately on startup
    try:
        initial = sensor_tick(sensors, store, alerts)
        logger.info("📊 Startup reading: %s", initial)
    except Exception:
        logger.exception("Initial sensor reading failed")

    while not stop_event.is_set():
        stop_event.wait(config.poll_interval_sec)
        if stop_event.is_set():
            break
        try:
            sensor_tick(sensors, store, alerts)
        except Exception:
            logger.exception("Sensor poll cycle failed")


def run_daily_loop(
    config: Config,
    store: Store,
    stop_event: threading.Event,
    alerts: AlertManager | None = None,
) -> None:
    """Background thread loop for daily photo schedule check."""
    logger.info(
        "📸 Daily timelapse photo scheduler active (scheduled for %02d:%02d)",
        config.daily_capture_hour,
        config.daily_capture_minute,
    )
    while not stop_event.is_set():
        try:
            daily_tick(config, store, alerts=alerts)
        except Exception:
            logger.exception("Daily photo check error")
        stop_event.wait(30)
