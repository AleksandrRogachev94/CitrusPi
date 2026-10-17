"""Pytest fixtures and configuration for CitrusPi test suite."""

from __future__ import annotations

import pytest

from citruspi import camera, runner


@pytest.fixture(autouse=True)
def _reset_module_state() -> None:
    """Reset module-level backoff timers to prevent test poisoning."""
    camera._last_capture_failure_time = 0.0
    runner._last_ai_failure_time = 0.0
    runner._ai_daily_attempts = 0
    runner._ai_attempt_date = ""
    runner._ai_permanent_failure_date = ""
    runner._last_dark_alert_date = ""
    runner._last_brightness_check_date = ""
    runner._last_missing_photo_alert_date = ""
    with camera._CAMERA_LOCK:
        camera._release_stream_handle_locked()
    camera._last_stream_activity = 0.0
