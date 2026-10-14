"""Tests for background runner ticks and loops."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from citruspi.alerts import AlertManager
from citruspi.config import Config
from citruspi.runner import daily_tick, sensor_tick
from citruspi.sensors import SensorHub
from citruspi.store import Store


def test_sensor_tick_records_reading_and_evaluates_alerts(tmp_path: Path) -> None:
    cfg = Config(
        mock_sensors=True,
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
        ntfy_topic="test-topic",
    )
    store = Store(cfg.data_dir)
    sensors = SensorHub(cfg)
    alerts = AlertManager(cfg)

    with patch.object(alerts, "evaluate") as mock_eval, patch.object(alerts, "send_heartbeat") as mock_hb:
        reading = sensor_tick(sensors, store, alerts)
        assert reading is not None
        assert reading.temp_f is not None

        # Verify reading stored in SQLite
        latest = store.latest()
        assert latest is not None
        assert latest["temp_f"] == reading.temp_f

        # Verify alert evaluation and heartbeat were dispatched
        mock_eval.assert_called_once_with(reading)
        mock_hb.assert_called_once()


def test_daily_tick_runs_scheduled_capture_and_analysis(tmp_path: Path) -> None:
    cfg = Config(
        mock_sensors=True,
        data_dir=tmp_path / "data",
        captures_dir=tmp_path / "captures",
        ai_enabled=True,
        ai_api_key="fake-key",
        daily_capture_hour=22,
        daily_capture_minute=0,
    )
    store = Store(cfg.data_dir)

    target_time = datetime(2026, 10, 14, 22, 1)

    with patch("citruspi.runner.run_daily_analysis") as mock_analysis:
        mock_analysis.return_value = {"status": "healthy", "summary": "Looking great"}
        photo = daily_tick(cfg, store, now=target_time)

        assert photo is not None
        assert photo.name == "2026-10-14.jpg"
        mock_analysis.assert_called_once_with(cfg, store, photo)
